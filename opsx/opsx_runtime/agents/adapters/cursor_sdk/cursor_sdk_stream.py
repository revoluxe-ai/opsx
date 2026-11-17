"""Live-лог Cursor SDK run: thinking, tool calls, deltas → std logging и файлы.

Используется repair и verify адаптерами вместо голого ``run.wait()``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import inspect
import json
import logging
import os
import sys
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import AsyncIterator, Sequence
from typing import Any, Mapping

from pydantic import BaseModel, ValidationError

from cursor_sdk.types import RunResult, RunStreamEvent

from collections.abc import Callable

from agents.config import ReadScopeViolation, paths_from_stream_record
from agents.log_setup import ensure_opsx_stderr_logger
from agents.budget.guard import BudgetExceeded, BudgetGuard
from agents.utils.response import is_agent_json_object, parse_agent_json

from agents.adapters.adapter_stream_constants import (  # noqa: F401
    CURSOR_HOP_CANCEL_DRAIN_SECONDS,
    CURSOR_HOP_HARD_DEADLINE_SECONDS,
    CURSOR_STREAM_CONVERSATION_RECOVERY_TIMEOUT_SECONDS,
    CURSOR_STREAM_GETRUN_POLL_SECONDS,
    CURSOR_STREAM_GETRUN_RPC_TIMEOUT_SECONDS,
    CURSOR_STREAM_IDLE_TIMEOUT_SECONDS,
    CURSOR_STREAM_USEFUL_PROGRESS_STALL_SECONDS,
    CURSOR_STREAM_WAIT_STATE_INTERVAL_SECONDS,
)
from workflows.stop_errors import OpsxStageStop
from agents.adapters.agent_stream import (  # noqa: F401
    compose_agent_stream_label,
    live_stream_log_level,
    stream_model_slug,
    stream_round_for_hop,
)

_TERMINAL_RUN_STATUSES = frozenset({"finished", "error", "cancelled", "expired"})


def _effective_stream_constant(name: str, default: float) -> float:
    """Читает тайминг из neutral ``adapter_stream_constants`` (monkeypatch в тестах)."""
    try:
        from agents.adapters import adapter_stream_constants as pub

        if hasattr(pub, name):
            return float(getattr(pub, name))
    except (ImportError, TypeError, ValueError):
        pass
    return default

# Tally неизвестных top-level ключей wire-события (только OPSX_STREAM_DEBUG=1).
_STREAM_DEBUG_UNKNOWN_TALLY: dict[str, int] = {}
_STREAM_DEBUG_UNKNOWN_TALLY_CAP = 32
_STREAM_DEBUG_FROM_JSON_PATCHED = False
_STREAM_DEBUG_UNKNOWN_BY_OFFSET: dict[str, str] = {}
_STREAM_UNKNOWN_DIAG_BY_OFFSET: dict[str, tuple[str, str]] = {}
_STREAM_UNKNOWN_DIAG_CAP = 64
_STREAM_UNKNOWN_STALL_THRESHOLD = 3

_LOGGER = logging.getLogger(__name__)
_STREAM_LOG_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="opsx-hop-log",
)
_STREAM_FILE_LOCKS: dict[str, threading.Lock] = {}
_LIVE_NDJSON_WIRE_BODY_MAX = 512


def configure_cursor_sdk_stream_logger(logger: logging.Logger | None = None) -> logging.Logger:
    """Настроить module-logger для live-log Cursor SDK (stderr, без propagate).

    По умолчанию INFO (хоповый поток); DEBUG — только при ``OPSX_STREAM_DEBUG=1``.
    """
    target = logger or _LOGGER
    debug = os.environ.get("OPSX_STREAM_DEBUG", "").strip() == "1"
    level = logging.DEBUG if debug else logging.INFO
    ensure_opsx_stderr_logger(target, level=level)
    return target


def _wire_body_for_log(raw: Mapping[str, Any]) -> str:
    """Полное wire-тело для ERROR-лога (без offset)."""
    payload = {str(k): v for k, v in raw.items() if str(k) != "offset"}
    return json.dumps(payload, ensure_ascii=False, default=str)


def _unknown_diag_storage_key(offset: str | None) -> str:
    """Ключ diag для ``record_event``: ``offset=None`` → ``__none__``."""
    if isinstance(offset, str) and offset.strip():
        return offset.strip()
    return "__none__"


def _remember_unknown_wire_diag(
    offset: str | None,
    raw: Mapping[str, Any],
) -> tuple[str, str]:
    discriminator = _unknown_stream_discriminator(raw)
    wire_body = _wire_body_for_log(raw)
    key = _unknown_diag_storage_key(offset)
    if len(_STREAM_UNKNOWN_DIAG_BY_OFFSET) >= _STREAM_UNKNOWN_DIAG_CAP:
        _STREAM_UNKNOWN_DIAG_BY_OFFSET.pop(next(iter(_STREAM_UNKNOWN_DIAG_BY_OFFSET)))
    _STREAM_UNKNOWN_DIAG_BY_OFFSET[key] = (discriminator, wire_body)
    return discriminator, wire_body


_READ_ONLY_STREAM_TOOLS = frozenset({"read", "grep", "glob", "ls"})


def useful_progress_stall_seconds_for_tools(
    tools: Sequence[str] | None,
) -> float | None:
    """Порог §27.0.3 для read-only hop; ``None`` — shell/pytest и длинные tool-gap.

    Длинные tool/shell-фазы часто шлют wire heartbeat без ``sdk_message`` —
    guard отключён, пока в tools есть не-read-only инструменты.
    """
    if not tools:
        return None
    normalized = {str(tool).strip().lower() for tool in tools if str(tool).strip()}
    if not normalized or normalized - _READ_ONLY_STREAM_TOOLS:
        return None
    return _effective_stream_constant(
        "CURSOR_STREAM_USEFUL_PROGRESS_STALL_SECONDS",
        CURSOR_STREAM_USEFUL_PROGRESS_STALL_SECONDS,
    )


configure_cursor_sdk_stream_logger(_LOGGER)

def _format_exception_cause(exc: BaseException) -> str:
    cause = exc.__cause__
    if cause is None:
        return "none"
    return f"{type(cause).__name__}: {cause}"


def log_cursor_hop_primary_exception(
    *,
    phase: str,
    label: str,
    run_id: str | None,
    agent_id: str | None,
    elapsed_seconds: float,
    exc: BaseException,
    rpc: str | None = None,
) -> None:
    """Структурированный ERROR перед wrap/invalidate (без conversation/context)."""
    configure_cursor_sdk_stream_logger(_LOGGER)
    _LOGGER.error(
        (
            "cursor hop primary failure: phase=%s rpc=%s hop=%s run_id=%s "
            "agent_id=%s elapsed_seconds=%.1f exception=%s: %s cause=%s"
        ),
        phase,
        rpc or "n/a",
        label,
        run_id or "unknown",
        agent_id or "unknown",
        elapsed_seconds,
        type(exc).__name__,
        exc,
        _format_exception_cause(exc),
        exc_info=True,
    )


def log_cursor_stream_stalled_on_unknown(
    *,
    hop_label: str,
    run_id: str | None,
    agent_id: str | None,
    consecutive_unknown: int,
    last_live_line: str,
    resume_attempt: int | None = None,
) -> None:
    """ERROR после серии ``stream_unknown`` без полезных событий (зависание observe)."""
    configure_cursor_sdk_stream_logger(_LOGGER)
    attempt = resume_attempt if resume_attempt is not None else "n/a"
    _LOGGER.error(
        "cursor stream stalled: consecutive_unknown=%s hop=%s run_id=%s agent_id=%s "
        "resume_attempt=%s last_live_line=%s",
        consecutive_unknown,
        hop_label,
        run_id or "unknown",
        agent_id or "unknown",
        attempt,
        last_live_line,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_stream_file_sync(path: Path, chunk: str) -> None:
    """Синхронная дозапись в live-log (вызывается из thread pool, не из event loop)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    lock = _STREAM_FILE_LOCKS.setdefault(key, threading.Lock())
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            _ = handle.write(chunk)


def _schedule_stream_file_append(path: Path, chunk: str) -> None:
    """Поставить дозапись live-log в очередь executor (не блокировать observe loop)."""
    try:
        loop = asyncio.get_running_loop()
        _ = loop.run_in_executor(_STREAM_LOG_EXECUTOR, _append_stream_file_sync, path, chunk)
    except RuntimeError:
        _append_stream_file_sync(path, chunk)


def _smoke_mirror_log_path() -> Path | None:
    """Путь из ``OPSX_SMOKE_MIRROR_LOG`` для дублирования live-строк smoke."""
    raw = os.environ.get("OPSX_SMOKE_MIRROR_LOG", "").strip()
    if not raw:
        return None
    return Path(raw)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            key: _json_safe(item)
            for key, item in asdict(value).items()
            if item is not None
        }
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _assistant_text(message: Any) -> str:
    content = getattr(getattr(message, "message", None), "content", ())
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", "")
        if text:
            parts.append(text)
    return "".join(parts)


def sdk_message_record(message: Any) -> dict[str, Any]:
    """Сериализовать ``SDKMessage`` в plain dict для NDJSON."""
    msg_type = getattr(message, "type", None)
    if msg_type == "thinking":
        return {
            "kind": "thinking",
            "text": getattr(message, "text", ""),
            "thinking_duration_ms": getattr(message, "thinking_duration_ms", None),
        }
    if msg_type == "tool_call":
        return {
            "kind": "tool_call",
            "name": getattr(message, "name", ""),
            "status": getattr(message, "status", ""),
            "call_id": getattr(message, "call_id", ""),
            "args": _json_safe(getattr(message, "args", None)),
            "result": _json_safe(getattr(message, "result", None)),
        }
    if msg_type == "status":
        return {
            "kind": "status",
            "status": getattr(message, "status", ""),
            "message": getattr(message, "message", ""),
        }
    if msg_type == "assistant":
        return {
            "kind": "assistant",
            "text": _assistant_text(message),
        }
    if msg_type == "usage":
        usage = getattr(message, "usage", None)
        return {"kind": "usage", "usage": _json_safe(usage)}
    if msg_type == "system":
        return {
            "kind": "system",
            "subtype": getattr(message, "subtype", ""),
            "model": _json_safe(getattr(message, "model", None)),
        }
    if msg_type == "request":
        return {
            "kind": "request",
            "request_id": getattr(message, "request_id", ""),
        }
    if msg_type == "user":
        return {"kind": "user", "payload": _json_safe(message)}
    if isinstance(message, Mapping):
        return {"kind": "sdk_message", "payload": _json_safe(dict(message))}
    return {"kind": msg_type or "sdk_message", "raw": _json_safe(message)}


def interaction_update_record(update: Any) -> dict[str, Any]:
    """Сериализовать ``InteractionUpdate`` (deltas, tool lifecycle)."""
    update_type = getattr(update, "type", "")
    record: dict[str, Any] = {"kind": "interaction", "update_type": update_type}
    if update_type == "thinking-delta":
        record["text"] = getattr(update, "text", "")
    elif update_type == "text-delta":
        record["text"] = getattr(update, "text", "")
    elif update_type in {
        "tool-call-started",
        "tool-call-completed",
        "partial-tool-call",
    }:
        record["call_id"] = getattr(update, "call_id", "")
        record["tool_call"] = _json_safe(getattr(update, "tool_call", {}))
    elif update_type == "thinking-completed":
        record["thinking_duration_ms"] = getattr(update, "thinking_duration_ms", None)
    else:
        record["payload"] = _json_safe(update)
    return record


def _live_log_prefix(label: str) -> str:
    if "|" in label:
        run_part, agent_part = label.split("|", 1)
        return f"[{run_part}][{agent_part}]"
    return f"[{label}]"


def _local_time_hms() -> str:
    """Локальное время для префикса live-строки (24ч, ``HH:mm:ss``)."""
    return datetime.now().astimezone().strftime("%H:%M:%S")


def _stream_debug_enabled() -> bool:
    """Включена ли отладка задержки стрима (``OPSX_STREAM_DEBUG=1``).

    При включении к thinking-строкам добавляются ``recv=`` (момент ``__anext__``)
    и ``emit=`` (момент записи в stderr/лог); в stderr пишется тип каждого recv-события.
    """
    return os.environ.get("OPSX_STREAM_DEBUG", "").strip() == "1"


def _unknown_stream_discriminator(raw: Mapping[str, Any]) -> str:
    """Имена top-level ключей wire-события без ``offset`` (для kind=unknown)."""
    keys = sorted(str(key) for key in raw if str(key) != "offset")
    return "+".join(keys) if keys else "empty"


def _note_unknown_stream_discriminator(discriminator: str) -> None:
    """Один раз на уникальный discriminator + счётчик (cap на число типов)."""
    tally = _STREAM_DEBUG_UNKNOWN_TALLY
    if discriminator not in tally:
        if len(tally) >= _STREAM_DEBUG_UNKNOWN_TALLY_CAP:
            return
        tally[discriminator] = 0
        _LOGGER.debug(
            "[OPSX_STREAM_DEBUG] unknown_wire discriminator=%r",
            discriminator,
        )
    tally[discriminator] += 1


def _patch_run_stream_event_from_json_for_debug() -> None:
    """Обёртка ``RunStreamEvent.from_json``: diag unknown + опциональный OPSX debug."""
    global _STREAM_DEBUG_FROM_JSON_PATCHED
    if _STREAM_DEBUG_FROM_JSON_PATCHED:
        return
    original = RunStreamEvent.from_json

    def from_json_with_unknown_diag(
        cls, value: Mapping[str, Any]
    ) -> RunStreamEvent:
        event = original(value)
        if event.kind == "unknown":
            disc, snippet = _remember_unknown_wire_diag(event.offset, value)
            if _stream_debug_enabled():
                _note_unknown_stream_discriminator(disc)
                _STREAM_DEBUG_UNKNOWN_BY_OFFSET[_unknown_diag_storage_key(event.offset)] = disc
        return event

    RunStreamEvent.from_json = classmethod(from_json_with_unknown_diag)  # type: ignore[method-assign, assignment]
    _STREAM_DEBUG_FROM_JSON_PATCHED = True


_patch_run_stream_event_from_json_for_debug()


def stream_debug_unknown_tally() -> dict[str, int]:
    """Снимок счётчиков unknown wire-событий (health / RCA)."""
    return dict(_STREAM_DEBUG_UNKNOWN_TALLY)


def _debug_log_stream_recv(event: RunStreamEvent) -> None:
    """stderr: метка recv и тип события (только ``OPSX_STREAM_DEBUG=1``)."""
    if not _stream_debug_enabled():
        return
    label_parts: list[str] = [f"kind={getattr(event, 'kind', '?')}"]
    if event.sdk_message is not None:
        label_parts.append(f"sdk={getattr(event.sdk_message, 'type', '?')}")
    if event.interaction_update is not None:
        label_parts.append(
            f"interaction={getattr(event.interaction_update, 'type', '?')}"
        )
    if event.step is not None:
        label_parts.append("step=1")
    if event.kind == "done":
        label_parts.append("done=1")
    if event.kind == "unknown":
        label_parts.append("wire=unknown")
    _LOGGER.debug(
        "[OPSX_STREAM_DEBUG] recv %s",
        " ".join(label_parts),
    )


def _is_thinking_stream_record(record: dict[str, Any]) -> bool:
    """True для thinking / thinking-delta записей live-log."""
    if record.get("kind") == "thinking":
        return True
    return (
        record.get("kind") == "interaction"
        and record.get("update_type") == "thinking-delta"
    )


def _stamp_live_line(body: str) -> str:
    """Добавить метку времени в начало непустой live-строки."""
    if not body:
        return ""
    return f"{_local_time_hms()} {body}"


def format_live_line(label: str, record: dict[str, Any]) -> str:
    """Одна человекочитаемая строка для stderr / ``.live.log`` (префикс ``HH:mm:ss``)."""
    kind = record.get("kind", "")
    prefix = _live_log_prefix(label)
    if kind == "thinking":
        text = str(record.get("text") or "").strip()
        if not text:
            return ""
        return _stamp_live_line(f"{prefix} thinking: {text}")
    if kind == "interaction" and record.get("update_type") == "thinking-delta":
        text = str(record.get("text") or "")
        if not text:
            return ""
        return _stamp_live_line(f"{prefix} thinkingΔ: {text}")
    if kind == "interaction" and record.get("update_type") == "text-delta":
        text = str(record.get("text") or "")
        if not text:
            return ""
        return _stamp_live_line(f"{prefix} textΔ: {text}")
    if kind == "tool_call":
        name = record.get("name") or "?"
        status = record.get("status") or "?"
        args = record.get("args")
        args_preview = json.dumps(args, ensure_ascii=False) if args else ""
        if len(args_preview) > 240:
            args_preview = args_preview[:237] + "..."
        line = f"{prefix} tool {name} [{status}]"
        if args_preview:
            line += f" args={args_preview}"
        if status in {"completed", "error"} and record.get("result") is not None:
            result_preview = json.dumps(
                record.get("result"),
                ensure_ascii=False,
            )
            if len(result_preview) > 320:
                result_preview = result_preview[:317] + "..."
            line += f" result={result_preview}"
        return _stamp_live_line(line)
    if kind == "interaction" and record.get("update_type") == "tool-call-started":
        tool_call = record.get("tool_call") or {}
        name = tool_call.get("name") or tool_call.get("toolName") or "?"
        return _stamp_live_line(f"{prefix} tool▶ {name}")
    if kind == "interaction" and record.get("update_type") == "tool-call-completed":
        tool_call = record.get("tool_call") or {}
        name = tool_call.get("name") or tool_call.get("toolName") or "?"
        return _stamp_live_line(f"{prefix} tool✓ {name}")
    if kind == "status":
        return _stamp_live_line(
            (
                f"{prefix} status={record.get('status')} "
                f"{record.get('message') or ''}".strip()
            )
        )
    if kind == "assistant":
        text = str(record.get("text") or "").strip()
        if not text:
            return ""
        if len(text) > 500:
            text = text[:497] + "..."
        return _stamp_live_line(f"{prefix} assistant: {text}")
    if kind == "run_meta":
        return _stamp_live_line(
            f"{prefix} run_id={record.get('run_id')} agent_id={record.get('agent_id')}"
        )
    if kind == "timeout":
        return _stamp_live_line(f"{prefix} TIMEOUT: {record.get('message')}")
    if kind == "terminal":
        message = str(record.get("message") or "").strip()
        line = f"{prefix} finished status={record.get('status')}"
        if message:
            line = f"{line} {message}"
        return _stamp_live_line(line)
    if kind == "get_run_poll":
        return _stamp_live_line(
            f"{prefix} get_run_poll status={record.get('status')!r} "
            f"observe_idle={record.get('observe_idle_seconds')}s"
        )
    if kind in {"system", "request", "user"}:
        return _stamp_live_line(
            f"{prefix} {kind}: {json.dumps(record, ensure_ascii=False)}"
        )
    if kind == "stream_unknown":
        parts = [
            f"{prefix} stream_unknown offset={record.get('offset')!r} "
            f"wire={record.get('wire_discriminator')!r}",
        ]
        if record.get("run_id"):
            parts.append(f"run_id={record.get('run_id')}")
        if record.get("agent_id"):
            parts.append(f"agent_id={record.get('agent_id')}")
        if record.get("wire_body"):
            parts.append(f"wire_body={record.get('wire_body')}")
        return _stamp_live_line(" ".join(parts))
    return _stamp_live_line(f"{prefix} {kind}: {json.dumps(record, ensure_ascii=False)}")


def account_tool_invocation_in_stream(
    record: Mapping[str, Any],
    counted_call_ids: set[str],
) -> bool:
    """Списать один tool invocation в hop budget (не дублировать running/completed).

    Cursor SDK отдаёт lifecycle tool call несколькими событиями (``tool-call-started``,
    ``tool_call`` со status running/completed). Учитываем **один** debit на ``call_id``.
    """
    kind = record.get("kind")
    if kind == "tool_call":
        name = str(record.get("name") or "").strip()
        if not name:
            return False
        call_id = str(record.get("call_id") or "").strip()
        status = str(record.get("status") or "").strip().lower()
        if call_id:
            if call_id in counted_call_ids:
                return False
            if status in {"", "running", "in_progress", "started", "pending", "completed", "success", "error", "failed"}:
                counted_call_ids.add(call_id)
                return True
            return False
        if status not in {"completed", "success", "error", "failed"}:
            return False
        return True
    if kind == "interaction":
        update_type = str(record.get("update_type") or "")
        if update_type != "tool-call-started":
            return False
        call_id = str(record.get("call_id") or "").strip()
        if call_id:
            if call_id in counted_call_ids:
                return False
            counted_call_ids.add(call_id)
            return True
        return True
    return False


class CursorRunStreamSink:
    """Пишет NDJSON + текстовый live-log через std logging (и файлы ``*.log``)."""

    def __init__(
        self,
        *,
        label: str,
        ndjson_path: Path | None,
        emit_stderr: bool = True,
        run_id: str | None = None,
        agent_id: str | None = None,
        tool_call_guard: Callable[[str, Mapping[str, Any]], None] | None = None,
        repo_root: Path | str | None = None,
        intra_run_budget_guard: BudgetGuard | None = None,
    ) -> None:
        self.label = label
        self.tool_call_guard = tool_call_guard
        self.intra_run_budget_guard = intra_run_budget_guard
        self._bound_run: Any = None
        self.repo_root = (
            Path(repo_root).resolve() if repo_root is not None else None
        )
        self.single_log_only = False
        if (
            ndjson_path is not None
            and ndjson_path.suffix == ".log"
            and ".live." not in ndjson_path.name
        ):
            self.ndjson_path = None
            self.text_path = ndjson_path
            self.single_log_only = True
        else:
            self.ndjson_path = ndjson_path
            self.text_path = (
                ndjson_path.with_suffix(".log") if ndjson_path is not None else None
            )
        self.emit_stderr = emit_stderr
        self.run_id = run_id
        self.agent_id = agent_id
        self._pending_recv_hms: str | None = None
        self.last_live_line: str = ""
        # Первый soft tool-result READ_SCOPE_VIOLATION (scoped read MCP), не guard exception.
        self.observed_outside_restrict_allow: ReadScopeViolation | None = None
        self._counted_tool_call_ids: set[str] = set()
        self._open_tool_call_ids: set[str] = set()
        if ndjson_path is not None:
            ndjson_path.parent.mkdir(parents=True, exist_ok=True)
        if self.text_path is not None:
            self.text_path.parent.mkdir(parents=True, exist_ok=True)

    def _append_ndjson(self, record: dict[str, Any]) -> None:
        if self.ndjson_path is None:
            return
        row = dict(record)
        wire_body = row.get("wire_body")
        if isinstance(wire_body, str) and len(wire_body) > _LIVE_NDJSON_WIRE_BODY_MAX:
            row["wire_body"] = wire_body[:_LIVE_NDJSON_WIRE_BODY_MAX] + "…"
        line = json.dumps(
            {"ts": _utc_now(), "label": self.label, **row},
            ensure_ascii=False,
            default=str,
        )
        _schedule_stream_file_append(self.ndjson_path, line + "\n")

    def _append_text(self, line: str) -> None:
        if not line:
            return
        mirror_path = _smoke_mirror_log_path()
        if mirror_path is not None:
            _schedule_stream_file_append(mirror_path, line + "\n")
        if self.text_path is None:
            return
        _schedule_stream_file_append(self.text_path, line + "\n")

    def emit(self, record: dict[str, Any]) -> None:
        self._append_ndjson(record)
        if not self.emit_stderr:
            return
        line = format_live_line(self.label, record)
        if not line:
            return
        if _stream_debug_enabled() and _is_thinking_stream_record(record):
            emit_hms = _local_time_hms()
            recv_hms = self._pending_recv_hms or "?"
            line = f"{line} recv={recv_hms} emit={emit_hms}"
            self._pending_recv_hms = None
        self._append_text(line)
        level = live_stream_log_level(record)
        _LOGGER.log(level, "%s", line)
        self.last_live_line = line

    def bind_run(self, run: Any) -> None:
        """Привязать run для cancel при intra-run budget breach."""
        self._bound_run = run

    def has_in_flight_tool_call(self) -> bool:
        """True, если в stream есть tool call без terminal status (shell/pytest gap)."""
        return bool(self._open_tool_call_ids)

    def _track_in_flight_tool_call(self, record: Mapping[str, Any]) -> None:
        """Учесть lifecycle tool call для useful-progress stall (без ложного kill)."""
        kind = record.get("kind")
        if kind == "tool_call":
            call_id = str(record.get("call_id") or "").strip()
            if not call_id:
                return
            status = str(record.get("status") or "").strip().lower()
            if status in {"", "running", "in_progress", "started", "pending"}:
                self._open_tool_call_ids.add(call_id)
            elif status in {
                "completed",
                "success",
                "error",
                "failed",
                "cancelled",
            }:
                self._open_tool_call_ids.discard(call_id)
            return
        if kind != "interaction":
            return
        update_type = str(record.get("update_type") or "")
        call_id = str(record.get("call_id") or "").strip()
        if not call_id:
            return
        if update_type == "tool-call-started":
            self._open_tool_call_ids.add(call_id)
        elif update_type == "tool-call-completed":
            self._open_tool_call_ids.discard(call_id)

    def _enforce_tool_guard(self, record: Mapping[str, Any]) -> None:
        if self.intra_run_budget_guard is not None and account_tool_invocation_in_stream(
            record,
            self._counted_tool_call_ids,
        ):
            self.intra_run_budget_guard.assert_tool_allowed()
        if self.tool_call_guard is None:
            return
        tool_name, paths = paths_from_stream_record(record)
        if not tool_name:
            return
        args: Mapping[str, Any] = {}
        if record.get("kind") == "tool_call":
            raw_args = record.get("args")
            if isinstance(raw_args, Mapping):
                args = raw_args
        elif record.get("kind") == "interaction":
            tool_call = record.get("tool_call") or {}
            if isinstance(tool_call, Mapping):
                raw_args = tool_call.get("args") or tool_call.get("arguments")
                if isinstance(raw_args, Mapping):
                    args = raw_args
        if not isinstance(args, Mapping):
            args = {}
        name_l = str(tool_name or "").strip().lower()
        if not paths and name_l not in {"shell", "glob", "grep", "ls"}:
            return
        from agents.config import ReadScopeViolation, read_scope_enforcement_mode
        from agents.read_scope_turn import enrich_violation

        try:
            import inspect

            guard = self.tool_call_guard
            if guard is not None and len(inspect.signature(guard).parameters) >= 3:
                guard(tool_name, args, record)
            else:
                guard(tool_name, args)
        except ReadScopeViolation as exc:
            violation = enrich_violation(
                exc,
                agent_id=self.agent_id,
                run_id=self.run_id,
            )
            command = str(args.get("command") or args.get("cmd") or "")
            self.emit(
                {
                    "kind": "read_scope_violation",
                    "tool": violation.tool,
                    "path": violation.path,
                    "rule": violation.rule,
                    "command": violation.command or command[:500],
                    "message": str(violation),
                }
            )
            if violation.rule == "outside_restrict_allow":
                self.observed_outside_restrict_allow = violation
            if read_scope_enforcement_mode() == "warn":
                return
            raise violation

    def _maybe_record_soft_outside_allow(self, record: Mapping[str, Any]) -> None:
        """Зафиксировать первый soft ``outside_restrict_allow`` из tool-result hop."""
        if self.observed_outside_restrict_allow is not None:
            return
        from agents.scope_tool_payload import read_scope_violation_from_stream_record

        violation = read_scope_violation_from_stream_record(
            record,
            agent_id=self.agent_id,
            run_id=self.run_id,
        )
        if violation is not None:
            self.observed_outside_restrict_allow = violation

    def _apply_intra_run_budget(self, record: Mapping[str, Any]) -> None:
        if self.intra_run_budget_guard is None:
            return
        if record.get("kind") != "usage":
            return
        usage = record.get("usage")
        if not isinstance(usage, Mapping):
            return
        payload = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_read_tokens": usage.get("cache_read_tokens"),
            "cache_write_tokens": usage.get("cache_write_tokens"),
        }
        self.intra_run_budget_guard.record_intra_run_usage(payload)

    def record_event(self, event: RunStreamEvent) -> None:
        if event.sdk_message is not None:
            record = sdk_message_record(event.sdk_message)
            self._track_in_flight_tool_call(record)
            self._enforce_tool_guard(record)
            self._maybe_record_soft_outside_allow(record)
            self._apply_intra_run_budget(record)
            self.emit(record)
            return
        if event.interaction_update is not None:
            record = interaction_update_record(event.interaction_update)
            self._track_in_flight_tool_call(record)
            self._enforce_tool_guard(record)
            self._maybe_record_soft_outside_allow(record)
            self.emit(record)
            return
        if event.step is not None:
            self.emit(
                {
                    "kind": "step",
                    "payload": _json_safe(event.step),
                }
            )
            return
        if event.kind == "result":
            self.emit({"kind": "terminal", "status": "result_envelope"})
            return
        if event.kind == "done":
            done_payload = event.done or {}
            status = str(done_payload.get("status") or "done")
            self.emit({"kind": "terminal", "status": status, "message": "done envelope"})
            return
        if event.kind == "unknown":
            offset_key = _unknown_diag_storage_key(event.offset)
            wire = _STREAM_DEBUG_UNKNOWN_BY_OFFSET.pop(offset_key, None)
            diag = _STREAM_UNKNOWN_DIAG_BY_OFFSET.pop(offset_key, None)
            wire_body = diag[1] if diag else ""
            if wire is None and diag:
                wire = diag[0]
            if wire is None and _STREAM_DEBUG_UNKNOWN_TALLY:
                wire = next(iter(_STREAM_DEBUG_UNKNOWN_TALLY))
            if wire is None:
                wire = "unknown"
            self.emit(
                {
                    "kind": "stream_unknown",
                    "offset": event.offset,
                    "wire_discriminator": wire,
                    "wire_body": wire_body,
                    "run_id": self.run_id,
                    "agent_id": self.agent_id,
                }
            )


async def await_next_cursor_run_event(
    event_iterator: AsyncIterator[RunStreamEvent],
    *,
    idle_timeout_seconds: float,
) -> RunStreamEvent:
    """Дождаться следующего события ``AsyncRun`` с ограничением простоя стрима.

    Args:
        event_iterator: Итератор событий ``run.__aiter__()``.
        idle_timeout_seconds: Максимальная пауза без новых событий (секунды).

    Returns:
        Следующее ``RunStreamEvent`` из стрима.

    Raises:
        asyncio.TimeoutError: если за ``idle_timeout_seconds`` не пришло событие.
        StopAsyncIteration: если стрим завершился.
    """
    return await asyncio.wait_for(
        event_iterator.__anext__(),
        timeout=idle_timeout_seconds,
    )


async def _maybe_await(value: Any) -> None:
    if inspect.isawaitable(value):
        await value


def _interaction_update_is_thinking_only(event: RunStreamEvent) -> bool:
    """True, если interaction — только thinking-delta без tool/shell."""
    update = event.interaction_update
    if update is None:
        return False
    update_type = str(
        getattr(update, "type", None)
        or getattr(update, "update_type", None)
        or "",
    ).lower()
    if update_type in {"tool-call-started", "tool-call-completed", "text-delta"}:
        return False
    return "thinking" in update_type


def _sdk_message_is_thinking_only(event: RunStreamEvent) -> bool:
    message = event.sdk_message
    if message is None:
        return False
    msg_type = str(getattr(message, "type", "") or "").lower()
    return msg_type == "thinking"


def _is_useful_stream_event(event: RunStreamEvent) -> bool:
    """True для событий, которые считаются прогрессом hop (не пустой wire/heartbeat)."""
    if event.sdk_message is not None:
        return True
    if event.interaction_update is not None:
        return True
    if event.step is not None:
        return True
    if event.kind in {"done", "result"}:
        return True
    return False


def _is_stall_progress_stream_event(event: RunStreamEvent) -> bool:
    """Прогресс для §27.0.3 stall-guard: tool/terminal, не редкий thinking-delta."""
    if event.kind in {"done", "result"}:
        return True
    if event.step is not None:
        return True
    if event.interaction_update is not None:
        if _interaction_update_is_thinking_only(event):
            return False
        return True
    if event.sdk_message is not None:
        if _sdk_message_is_thinking_only(event):
            return False
        return True
    return False


def _run_status_str(status: Any) -> str:
    return str(getattr(status, "value", status) or "")


async def _run_result_from_fetched(run: Any, fetched: Any) -> RunResult:
    """Собрать ``RunResult`` из GetRun и применить side-effects SDK на ``run``."""
    run_id = str(getattr(run, "id", "") or getattr(fetched, "id", "") or "")
    status = str(getattr(fetched, "status", "") or "")
    run_result = RunResult(
        id=str(getattr(fetched, "id", "") or run_id),
        agent_id=str(
            getattr(fetched, "agent_id", "") or getattr(run, "agent_id", "")
        ),
        status=getattr(fetched, "status", status),
        result=str(getattr(fetched, "result", "") or ""),
        model=getattr(fetched, "model", None),
        duration_ms=int(getattr(fetched, "duration_ms", 0) or 0),
        git=getattr(fetched, "git", None),
        created_at=getattr(fetched, "created_at", None),
        usage=getattr(fetched, "usage", None),
    )
    apply_result = getattr(run, "_apply_result", None)
    result_with_usage = getattr(run, "_result_with_accumulated_usage", None)
    if callable(apply_result) and callable(result_with_usage):
        for callback in apply_result(run_result):
            await _maybe_await(callback())
        run._terminal_result = run_result
        return result_with_usage(run_result)
    return run_result


def _parse_conversation_payload(raw: str) -> Any | None:
    """Распарсить JSON ``conversation_json()``; ``None`` при пустом/битом теле."""
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _conversation_terminal_assistant_text(
    conversation: Any,
    *,
    payload_model: type[BaseModel] | None = None,
) -> str | None:
    """Текст последнего ``assistantMessage``, если turn заканчивается им (не toolCall).

    Используется как fallback-result, когда GetRun=``running``, но история уже
    содержит финальный ответ агента (observe не видел tool-raунды).

    При ``payload_model`` checkpoint принимается только полный JSON по Pydantic-схеме
    (как ``ABCAdapter.run``). Без модели — только цельный JSON-объект, не prose
    с подстрокой ``IMPL_OK`` / ``✅ IMPL_OK``.
    """
    if not isinstance(conversation, list):
        return None
    last_assistant: str | None = None
    last_step_type: str | None = None
    for item in conversation:
        if not isinstance(item, dict):
            continue
        turn = item.get("turn")
        if not isinstance(turn, dict):
            continue
        steps = turn.get("steps")
        if not isinstance(steps, list) or not steps:
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_type = str(step.get("type") or "")
            last_step_type = step_type
            if step_type == "assistantMessage":
                message = step.get("message")
                if isinstance(message, dict):
                    text = str(message.get("text") or "").strip()
                    if text:
                        last_assistant = text
    if last_step_type != "assistantMessage" or not last_assistant:
        return None
    if payload_model is not None:
        try:
            parsed = parse_agent_json(last_assistant, payload_model)
        except (ValidationError, ValueError):
            return None
        return json.dumps(parsed.model_dump(mode="json"), ensure_ascii=False)
    if not is_agent_json_object(last_assistant):
        return None
    return last_assistant


async def _run_result_from_conversation_text(run: Any, result_text: str) -> RunResult:
    """Собрать терминальный ``RunResult`` из checkpoint-ответа ``conversation_json``."""
    run_result = RunResult(
        id=str(getattr(run, "id", "") or ""),
        agent_id=str(getattr(run, "agent_id", "") or ""),
        status="finished",
        result=result_text,
    )
    apply_result = getattr(run, "_apply_result", None)
    result_with_usage = getattr(run, "_result_with_accumulated_usage", None)
    if callable(apply_result) and callable(result_with_usage):
        for callback in apply_result(run_result):
            await _maybe_await(callback())
        run._terminal_result = run_result
        return result_with_usage(run_result)
    return run_result


async def _try_recover_via_conversation_json(
    run: Any,
    *,
    payload_model: type[BaseModel] | None = None,
) -> RunResult | None:
    """GetRunConversation checkpoint, если GetRun застрял в ``running``."""
    conv_fn = getattr(run, "conversation_json", None)
    if not callable(conv_fn):
        return None
    try:
        raw = await asyncio.wait_for(
            conv_fn(),
            timeout=CURSOR_STREAM_CONVERSATION_RECOVERY_TIMEOUT_SECONDS,
        )
    except (asyncio.TimeoutError, Exception):
        return None
    if not isinstance(raw, str):
        return None
    loaded = _parse_conversation_payload(raw)
    assistant_text = _conversation_terminal_assistant_text(
        loaded,
        payload_model=payload_model,
    )
    if not assistant_text:
        return None
    return await _run_result_from_conversation_text(run, assistant_text)


async def _fetch_get_run_bounded(
    run: Any,
    *,
    timeout_seconds: float = CURSOR_STREAM_GETRUN_RPC_TIMEOUT_SECONDS,
) -> Any:
    """GetRun с bounded RPC timeout."""
    run_id = str(getattr(run, "id", "") or "")
    client = getattr(run, "client", None)
    if not run_id or client is None:
        raise asyncio.TimeoutError("GetRun unavailable: missing run id or client")
    return await asyncio.wait_for(client.get_run(run_id), timeout=timeout_seconds)


async def _executor_status(run: Any) -> str | None:
    """Текущий SDK status через GetRun; ``None`` при ошибке RPC."""
    try:
        fetched = await _fetch_get_run_bounded(run)
    except Exception:
        return None
    return str(getattr(fetched, "status", "") or "") or None


async def _executor_still_running(run: Any) -> bool:
    """True, если GetRun не подтвердил терминал (fail-closed при ошибке RPC)."""
    status = await _executor_status(run)
    if status is None:
        return True
    return status not in _TERMINAL_RUN_STATUSES


async def _maybe_report_consecutive_unknown_stall(
    run: Any,
    *,
    hop_label: str,
    run_id: str | None,
    agent_id: str | None,
    consecutive_unknown: int,
    last_unknown_live_line: str,
    observe_resume_count: int,
) -> int:
    """Порог ``stream_unknown``: stall ERROR только если GetRun не ``running``.

    Длинные tool/shell-фазы часто шлют wire heartbeat без ``sdk_message`` —
    это не зависание observe, пока исполнитель жив (тот же критерий, что
    ``_restart_observe_if_needed`` / reattach ObserveRun).

    Returns:
        Обновлённый ``consecutive_unknown`` (сброс в 0 при живом executor).
    """
    if consecutive_unknown < _STREAM_UNKNOWN_STALL_THRESHOLD:
        return consecutive_unknown
    if consecutive_unknown != _STREAM_UNKNOWN_STALL_THRESHOLD:
        return consecutive_unknown
    if await _executor_still_running(run):
        _LOGGER.debug(
            (
                "cursor stream unknown burst while executor running: "
                "consecutive_unknown=%s hop=%s run_id=%s agent_id=%s "
                "(not stalled; GetRun=running)"
            ),
            consecutive_unknown,
            hop_label,
            run_id or "unknown",
            agent_id or "unknown",
        )
        return 0
    resume_attempt = observe_resume_count if observe_resume_count > 0 else None
    log_cursor_stream_stalled_on_unknown(
        hop_label=hop_label,
        run_id=run_id,
        agent_id=agent_id,
        consecutive_unknown=consecutive_unknown,
        last_live_line=last_unknown_live_line,
        resume_attempt=resume_attempt,
    )
    return consecutive_unknown


async def _await_cancelled_tasks(*tasks: asyncio.Task[Any]) -> None:
    """Cancel и дождаться фоновых task (сохранить primary error снаружи)."""
    for task in tasks:
        if not task.done():
            task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _poll_get_run_after_observe_idle(
    run: Any,
    *,
    idle_seconds: float,
    payload_model: type[BaseModel] | None = None,
) -> RunResult | None:
    """Фоновый GetRun после useful-idle observe (tool-phase без событий стрима).

    Returns:
        ``RunResult``, если сервер уже в терминальном статусе или checkpoint
        ``conversation_json`` содержит финальный ``assistantMessage``; ``None``,
        если GetRun=``running`` и checkpoint ещё не готов — hop **не** отменяется.

    Raises:
        asyncio.TimeoutError: нет ``run.id`` или client для GetRun.
    """
    run_id = str(getattr(run, "id", "") or "")
    client = getattr(run, "client", None)
    if not run_id or client is None:
        raise asyncio.TimeoutError(
            f"observe idle {idle_seconds}s "
            "(cannot call GetRun: missing run id or client)"
        )
    try:
        fetched = await _fetch_get_run_bounded(run)
    except asyncio.TimeoutError as exc:
        raise asyncio.TimeoutError(
            f"GetRun RPC timed out after {CURSOR_STREAM_GETRUN_RPC_TIMEOUT_SECONDS}s "
            f"(observe useful-idle {idle_seconds}s)"
        ) from exc
    status = str(getattr(fetched, "status", "") or "")
    if status in _TERMINAL_RUN_STATUSES:
        return await _run_result_from_fetched(run, fetched)
    return await _try_recover_via_conversation_json(run, payload_model=payload_model)


async def _try_terminal_via_get_run(
    run: Any,
    *,
    payload_model: type[BaseModel] | None = None,
) -> RunResult | None:
    """Один GetRun перед CancelRun на hop deadline (arbiter без отмены running)."""
    run_id = str(getattr(run, "id", "") or "")
    client = getattr(run, "client", None)
    if not run_id or client is None:
        return None
    try:
        fetched = await asyncio.wait_for(client.get_run(run_id), timeout=15.0)
    except (asyncio.TimeoutError, Exception):
        return None
    status = str(getattr(fetched, "status", "") or "")
    if status in _TERMINAL_RUN_STATUSES:
        return await _run_result_from_fetched(run, fetched)
    return await _try_recover_via_conversation_json(run, payload_model=payload_model)


async def _recover_run_after_stream_idle(
    run: Any,
    *,
    idle_seconds: float,
    payload_model: type[BaseModel] | None = None,
) -> RunResult:
    """После idle-таймаута GetRun и вернуть терминальный результат (legacy API).

    Raises:
        asyncio.TimeoutError: GetRun=``running`` или нет client/run id.
    """
    polled = await _poll_get_run_after_observe_idle(
        run,
        idle_seconds=idle_seconds,
        payload_model=payload_model,
    )
    if polled is not None:
        return polled
    raise asyncio.TimeoutError(
        f"observe idle {idle_seconds}s (GetRun status=running; hop continues)"
    )


async def _cancel_run_by_id_only(run: Any) -> None:
    """CancelRun только по ``run.id`` (без agent-scoped id)."""
    run_id = str(getattr(run, "id", "") or "")
    client = getattr(run, "client", None)
    if not run_id or client is None:
        return
    cancel = getattr(client, "cancel_run", None)
    if not callable(cancel):
        return
    await cancel(run_id)


async def _arbiter_terminal_result(run: Any, *, preferred: RunResult | None) -> RunResult | None:
    """GetRun как арбiter, если ``wait``/cancel дали неоднозначный статус."""
    if preferred is not None and _run_status_str(preferred.status) in _TERMINAL_RUN_STATUSES:
        return preferred
    run_id = str(getattr(run, "id", "") or "")
    client = getattr(run, "client", None)
    if not run_id or client is None:
        return preferred
    try:
        fetched = await asyncio.wait_for(client.get_run(run_id), timeout=15.0)
    except (asyncio.TimeoutError, Exception):
        return preferred
    status = str(getattr(fetched, "status", "") or "")
    if status not in _TERMINAL_RUN_STATUSES:
        return preferred
    return await _run_result_from_fetched(run, fetched)


def _observe_event_iterator(run: Any) -> AsyncIterator[RunStreamEvent] | None:
    """Итератор ``ObserveRun`` (отдельный RPC, не send-stream ``__aiter__``)."""
    observe = getattr(run, "observe", None)
    if not callable(observe):
        return None
    return observe().__aiter__()


async def _aclose_observe_iterator(
    observe_iter: AsyncIterator[RunStreamEvent] | None,
) -> None:
    """Явно закрыть ObserveRun async-generator до drain task (Windows/SDK teardown)."""
    if observe_iter is None:
        return
    aclose = getattr(observe_iter, "aclose", None)
    if not callable(aclose):
        return
    try:
        await aclose()
    except (GeneratorExit, RuntimeError) as exc:
        _LOGGER.debug(
            "observe iterator aclose suppressed during hop teardown: %s",
            exc,
        )


def observe_task_read_scope_violation(
    observe_task: asyncio.Task[Any],
) -> Any | None:
    """Вернуть ``ReadScopeViolation`` из завершённого observe, иначе ``None``.

    Контракт транспорта: scope-enforce не должен маскироваться под ``run.wait()=cancelled``.
    """
    if not observe_task.done():
        return None
    exc = observe_task.exception()
    if exc is None:
        return None
    from agents.config import ReadScopeViolation  # noqa: PLC0415

    if isinstance(exc, ReadScopeViolation):
        return exc
    return None


async def _abort_on_useful_progress_stall(
    run: Any,
    *,
    sink: CursorRunStreamSink,
    label: str,
    last_useful_at: float,
    wait_task: asyncio.Task[Any],
) -> None:
    """CancelRun + типизированный ``OpsxStageStop`` при §27.0.3 (не ждать GetRun=running)."""
    loop = asyncio.get_running_loop()
    useful_idle = loop.time() - last_useful_at
    executor_status = await _executor_status(run)
    await _cancel_run_by_id_only(run)
    sink.emit(
        {
            "kind": "stall",
            "code": "CURSOR_STREAM_USEFUL_PROGRESS_STALL",
            "useful_idle_seconds": round(useful_idle, 1),
            "executor_status": executor_status or "unknown",
            "run_id": sink.run_id,
            "agent_id": sink.agent_id,
            "message": (
                f"useful progress stall {useful_idle:.0f}s "
                f"(GetRun={executor_status or 'unknown'}); hop cancelled"
            ),
        }
    )
    wait_task.cancel()
    raise OpsxStageStop(
        f"Cursor hop useful progress stall ({useful_idle:.0f}s)",
        code="CURSOR_STREAM_USEFUL_PROGRESS_STALL",
        stage=label,
        evidence=(
            f"hop={label} run_id={sink.run_id or 'unknown'} "
            f"agent_id={sink.agent_id or 'unknown'} "
            f"useful_idle_seconds={useful_idle:.1f} "
            f"executor_status={executor_status or 'unknown'}"
        ),
        owner="infra",
        next_action=(
            "opsx stop; opsx start for new code, "
            "then retry classifier hop or check Cursor provider health"
        ),
    )


async def wait_cursor_run_with_live_log(
    run: Any,
    *,
    timeout_seconds: float,
    label: str,
    stream_log: str | Path | None = None,
    emit_stderr: bool = True,
    payload_model: type[BaseModel] | None = None,
    tool_call_guard: Callable[[str, Mapping[str, Any]], None] | None = None,
    repo_root: Path | str | None = None,
    observed_soft_scope_violation_out: list[Any] | None = None,
    intra_run_budget_guard: BudgetGuard | None = None,
    useful_progress_stall_seconds: float | None = None,
) -> Any:
    """``run.wait()`` в foreground; live-log через ``observe()`` без гонки с send-stream.

    ``AsyncRun.wait()`` и ``__aiter__`` делят один async-generator на send-stream —
    параллельный ``__anext__`` и ``wait()`` даёт ``RuntimeError: anext(): … already
    running``. Завершение hop — ``create_task(run.wait())`` и monotonic hop deadline
    (``timeout_seconds``, типично ``CURSOR_HOP_HARD_DEADLINE_SECONDS`` ≈ 900 с).
    Live-log читает ``run.observe()`` (ObserveRun), не send-stream. Пустые
    wire/heartbeat/stream_unknown не сбрасывают useful-idle; редкий
    thinking-delta тоже не продлевает §27.0.3 (только tool/terminal/assistant).

    При useful-idle ≥ ``CURSOR_STREAM_IDLE_TIMEOUT_SECONDS`` — фоновый GetRun
    (``CURSOR_STREAM_GETRUN_POLL_SECONDS``): если ``running`` — дополнительно
    ``conversation_json`` checkpoint; hop продолжается без CancelRun, пока нет
    терминала GetRun или финального assistant в checkpoint. На hop deadline —
    последний GetRun/checkpoint, затем CancelRun + ``wait_live_run`` + arbiter.

    Если задан ``useful_progress_stall_seconds`` (типично 300 для read-only tools)
    и нет in-flight tool call — при превышении порога без полезных событий hop
    отменяется с ``OpsxStageStop`` (§27.0.3), без ожидания 600 с idle / GetRun.
    """
    ndjson_path = Path(stream_log) if stream_log else None
    sink = CursorRunStreamSink(
        label=label,
        ndjson_path=ndjson_path,
        emit_stderr=emit_stderr,
        run_id=str(getattr(run, "id", "") or "") or None,
        agent_id=str(getattr(run, "agent_id", "") or "") or None,
        tool_call_guard=tool_call_guard,
        repo_root=repo_root,
        intra_run_budget_guard=intra_run_budget_guard,
    )
    sink.bind_run(run)

    overall_timeout_seconds = timeout_seconds
    idle_timeout_seconds = _effective_stream_constant(
        "CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        CURSOR_STREAM_IDLE_TIMEOUT_SECONDS,
    )
    getrun_poll_seconds = min(
        idle_timeout_seconds,
        _effective_stream_constant(
            "CURSOR_STREAM_GETRUN_POLL_SECONDS",
            CURSOR_STREAM_GETRUN_POLL_SECONDS,
        ),
    )

    recovered_via_idle: list[RunResult] = []

    async def _complete_via_get_run(
        terminal: RunResult,
        *,
        message: str,
        wait_task: asyncio.Task[Any],
    ) -> None:
        await _emit_terminal_result(terminal, message=message)
        recovered_via_idle.append(terminal)
        wait_task.cancel()

    async def _emit_terminal_result(terminal: RunResult, *, message: str) -> None:
        detail = str(getattr(terminal, "result", "") or "").strip()
        if detail:
            message = f"{message}: {detail[:500]}"
        sink.emit(
            {
                "kind": "terminal",
                "status": _run_status_str(terminal.status),
                "message": message,
            }
        )

    async def _log_run_meta(meta_logged: bool) -> bool:
        if meta_logged:
            return True
        if getattr(run, "id", "") and getattr(run, "agent_id", ""):
            sink.emit(
                {
                    "kind": "run_meta",
                    "run_id": str(run.id),
                    "agent_id": str(run.agent_id),
                }
            )
            return True
        return meta_logged

    async def _record_stream_event(event: RunStreamEvent, meta_logged: bool) -> bool:
        nonlocal consecutive_unknown, last_unknown_live_line
        meta_logged = await _log_run_meta(meta_logged)
        if _stream_debug_enabled():
            sink._pending_recv_hms = _local_time_hms()
            _debug_log_stream_recv(event)
        try:
            sink.record_event(event)
        except Exception as scope_exc:
            from agents.config import ReadScopeViolation  # noqa: PLC0415

            if isinstance(scope_exc, (ReadScopeViolation, BudgetExceeded)):
                await _cancel_run_by_id_only(run)
            raise
        if event.kind == "unknown":
            consecutive_unknown += 1
            last_unknown_live_line = sink.last_live_line
            consecutive_unknown = await _maybe_report_consecutive_unknown_stall(
                run,
                hop_label=label,
                run_id=sink.run_id,
                agent_id=sink.agent_id,
                consecutive_unknown=consecutive_unknown,
                last_unknown_live_line=last_unknown_live_line,
                observe_resume_count=observe_resume_count,
            )
        elif _is_useful_stream_event(event):
            consecutive_unknown = 0
        return meta_logged

    meta_logged = False
    last_idle_diagnostic_at = 0.0
    last_wait_state_at = 0.0
    last_accepted_event_at = 0.0
    last_useful_at = 0.0
    consecutive_unknown = 0
    last_unknown_live_line = ""
    observe_resume_count = 0

    async def _on_overall_timeout(exc: asyncio.TimeoutError) -> None:
        await _cancel_run_by_id_only(run)
        preferred: RunResult | None = None
        client = getattr(run, "client", None)
        wait_live = getattr(client, "wait_live_run", None) if client else None
        run_id = str(getattr(run, "id", "") or "")
        try:
            if callable(wait_live) and run_id:
                preferred = await asyncio.wait_for(
                    wait_live(run_id),
                    timeout=CURSOR_HOP_CANCEL_DRAIN_SECONDS,
                )
        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
            preferred = None
        await _arbiter_terminal_result(run, preferred=preferred)

    async def _emit_wait_state(*, executor_status: str | None) -> None:
        nonlocal last_wait_state_at
        loop = asyncio.get_running_loop()
        now = loop.time()
        last_wait_state_at = now
        sink.emit(
            {
                "kind": "wait_state",
                "executor_status": executor_status or "unknown",
                "useful_idle_seconds": round(now - last_useful_at, 1),
                "since_last_useful_seconds": round(now - last_useful_at, 1),
                "since_last_event_seconds": round(
                    now - (last_accepted_event_at or last_useful_at),
                    1,
                ),
                "message": (
                    "hop waiting: observe idle; polling GetRun — "
                    "silence is not completion"
                ),
            }
        )

    async def _maybe_poll_get_run_on_idle(
        wait_task: asyncio.Task[Any],
    ) -> bool:
        """GetRun после useful-idle; ``True`` если hop завершён через recovery."""
        nonlocal last_idle_diagnostic_at, consecutive_unknown
        loop = asyncio.get_running_loop()
        if loop.time() - last_useful_at < idle_timeout_seconds:
            return False
        if loop.time() - last_idle_diagnostic_at < getrun_poll_seconds:
            return False
        last_idle_diagnostic_at = loop.time()
        try:
            polled = await _poll_get_run_after_observe_idle(
                run,
                idle_seconds=idle_timeout_seconds,
                payload_model=payload_model,
            )
        except asyncio.TimeoutError as poll_exc:
            sink.emit(
                {
                    "kind": "timeout",
                    "message": str(poll_exc),
                }
            )
            return False
        if polled is not None:
            await _complete_via_get_run(
                polled,
                message="recovered via GetRun/conversation after useful observe idle",
                wait_task=wait_task,
            )
            return True
        executor_status = await _executor_status(run)
        if executor_status == "running":
            consecutive_unknown = 0
        sink.emit(
            {
                "kind": "get_run_poll",
                "status": executor_status or "unknown",
                "observe_idle_seconds": idle_timeout_seconds,
                "message": (
                    f"observe silent {idle_timeout_seconds}s; "
                    f"GetRun={executor_status or 'unknown'} — hop continues"
                ),
            }
        )
        return False

    async def _observe_sidecar(wait_task: asyncio.Task[Any]) -> None:
        from agents.config import ReadScopeViolation  # noqa: PLC0415

        nonlocal meta_logged, last_useful_at, last_idle_diagnostic_at
        nonlocal recovered_via_idle, observe_resume_count, last_accepted_event_at
        loop = asyncio.get_running_loop()
        observe_iter = _observe_event_iterator(run)
        scope_violation: ReadScopeViolation | None = None
        budget_violation: BudgetExceeded | None = None
        if observe_iter is not None and last_useful_at <= 0.0:
            last_useful_at = loop.time()
        try:
            while not wait_task.done() and not recovered_via_idle:
                if (
                    useful_progress_stall_seconds is not None
                    and loop.time() - last_useful_at >= useful_progress_stall_seconds
                    and not sink.has_in_flight_tool_call()
                ):
                    await _abort_on_useful_progress_stall(
                        run,
                        sink=sink,
                        label=label,
                        last_useful_at=last_useful_at,
                        wait_task=wait_task,
                    )
                if await _maybe_poll_get_run_on_idle(wait_task):
                    return

                if (
                    loop.time() - last_wait_state_at
                    >= CURSOR_STREAM_WAIT_STATE_INTERVAL_SECONDS
                    and loop.time() - last_useful_at >= getrun_poll_seconds
                ):
                    await _emit_wait_state(executor_status=await _executor_status(run))

                if observe_iter is None:
                    await asyncio.sleep(0.05)
                    continue

                read_timeout = max(
                    0.05,
                    idle_timeout_seconds - (loop.time() - last_useful_at),
                )
                try:
                    event = await asyncio.wait_for(
                        observe_iter.__anext__(),
                        timeout=read_timeout,
                    )
                except asyncio.TimeoutError:
                    continue
                except StopAsyncIteration:
                    await _aclose_observe_iterator(observe_iter)
                    observe_iter = None
                    return

                last_accepted_event_at = loop.time()
                try:
                    meta_logged = await _record_stream_event(event, meta_logged)
                except BudgetExceeded as violation:
                    budget_violation = violation
                    break
                except ReadScopeViolation as violation:
                    scope_violation = violation
                    break
                if _is_stall_progress_stream_event(event):
                    last_useful_at = loop.time()
                    last_idle_diagnostic_at = 0.0
                elif _is_useful_stream_event(event):
                    last_idle_diagnostic_at = 0.0
                if event.kind in {"done", "result"}:
                    sink.emit(
                        {
                            "kind": "observe_signal",
                            "wire_kind": event.kind,
                            "message": (
                                "observe terminal envelope ignored until "
                                "run.wait() or GetRun confirms outcome"
                            ),
                        }
                    )
        finally:
            await _aclose_observe_iterator(observe_iter)
        if budget_violation is not None:
            raise budget_violation
        if scope_violation is not None:
            raise scope_violation

    async def _restart_observe_if_needed(
        wait_task: asyncio.Task[Any],
        observe_task: asyncio.Task[Any],
    ) -> asyncio.Task[Any]:
        """Возобновить ObserveRun на том же run, если исполнитель ещё жив."""
        nonlocal observe_resume_count
        if wait_task.done() or recovered_via_idle:
            return observe_task
        if not observe_task.done():
            return observe_task
        ob_exc = observe_task.exception()
        if ob_exc is not None:
            log_cursor_hop_primary_exception(
                phase="observe",
                rpc="ObserveRun",
                label=label,
                run_id=sink.run_id,
                agent_id=sink.agent_id,
                elapsed_seconds=loop.time() - hop_started,
                exc=ob_exc,
            )
            if isinstance(ob_exc, BudgetExceeded):
                return observe_task
            if not await _executor_still_running(run):
                return observe_task
            if _observe_event_iterator(run) is None:
                return observe_task
            observe_resume_count += 1
            sink.emit(
                {
                    "kind": "observe_resume",
                    "attempt": observe_resume_count,
                    "message": (
                        "observe failed while executor still running; "
                        "reattaching ObserveRun on same run"
                    ),
                }
            )
            return asyncio.create_task(_observe_sidecar(wait_task))
        if await _executor_still_running(run) and _observe_event_iterator(run) is not None:
            observe_resume_count += 1
            sink.emit(
                {
                    "kind": "observe_resume",
                    "attempt": observe_resume_count,
                    "message": (
                        "observe stream ended while executor still running; "
                        "reattaching ObserveRun on same run"
                    ),
                }
            )
            return asyncio.create_task(_observe_sidecar(wait_task))
        return observe_task

    loop = asyncio.get_running_loop()
    hop_started = loop.time()
    hop_deadline = hop_started + overall_timeout_seconds
    last_useful_at = hop_started
    wait_task = asyncio.create_task(run.wait())
    observe_task = asyncio.create_task(_observe_sidecar(wait_task))
    primary_exc: BaseException | None = None
    try:
        while not wait_task.done() and not recovered_via_idle:
            if (
                useful_progress_stall_seconds is not None
                and loop.time() - last_useful_at >= useful_progress_stall_seconds
                and not sink.has_in_flight_tool_call()
            ):
                await _abort_on_useful_progress_stall(
                    run,
                    sink=sink,
                    label=label,
                    last_useful_at=last_useful_at,
                    wait_task=wait_task,
                )
            observe_task = await _restart_observe_if_needed(wait_task, observe_task)
            scope_violation = observe_task_read_scope_violation(observe_task)
            if scope_violation is not None:
                raise scope_violation
            if observe_task.done():
                ob_exc = observe_task.exception()
                if isinstance(ob_exc, OpsxStageStop):
                    raise ob_exc
                if ob_exc is not None and _observe_event_iterator(run) is None:
                    raise ob_exc
                if ob_exc is not None and not await _executor_still_running(run):
                    raise ob_exc
            if loop.time() >= hop_deadline:
                terminal_via_get = await _try_terminal_via_get_run(
                    run,
                    payload_model=payload_model,
                )
                if terminal_via_get is not None:
                    await _complete_via_get_run(
                        terminal_via_get,
                        message="recovered via GetRun/conversation at hop deadline",
                        wait_task=wait_task,
                    )
                    break
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        _on_overall_timeout(
                            asyncio.TimeoutError(
                                f"overall run exceeded {overall_timeout_seconds}s"
                            )
                        ),
                        timeout=CURSOR_HOP_CANCEL_DRAIN_SECONDS + 20.0,
                    )
                message = f"exceeded {timeout_seconds}s"
                sink.emit(
                    {
                        "kind": "timeout",
                        "message": message,
                    }
                )
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(
                        persist_run_conversation(run, stream_log=ndjson_path),
                        timeout=10.0,
                    )
                raise asyncio.TimeoutError(message)
            await asyncio.sleep(0.05)
        scope_violation = observe_task_read_scope_violation(observe_task)
        if scope_violation is not None:
            if (
                observed_soft_scope_violation_out is not None
                and sink.observed_outside_restrict_allow is not None
            ):
                observed_soft_scope_violation_out.append(
                    sink.observed_outside_restrict_allow,
                )
            elif observed_soft_scope_violation_out is not None:
                observed_soft_scope_violation_out.append(scope_violation)
            raise scope_violation
        if observe_task.done():
            ob_exc = observe_task.exception()
            if isinstance(ob_exc, OpsxStageStop):
                raise ob_exc
        if recovered_via_idle:
            terminal = recovered_via_idle[0]
        elif wait_task.cancelled():
            ob_exc = observe_task.exception() if observe_task.done() else None
            if isinstance(ob_exc, OpsxStageStop):
                raise ob_exc
            raise asyncio.CancelledError(
                "run.wait cancelled after useful-progress stall"
            )
        else:
            terminal = wait_task.result()
            await _emit_terminal_result(terminal, message="run.wait() completed")
        await persist_run_conversation(run, stream_log=ndjson_path)
        if (
            observed_soft_scope_violation_out is not None
            and sink.observed_outside_restrict_allow is not None
        ):
            observed_soft_scope_violation_out.append(sink.observed_outside_restrict_allow)
        return terminal
    except BaseException as exc:
        primary_exc = exc
        log_cursor_hop_primary_exception(
            phase="wait",
            rpc="run.wait",
            label=label,
            run_id=sink.run_id,
            agent_id=sink.agent_id,
            elapsed_seconds=loop.time() - hop_started,
            exc=exc,
        )
        raise
    finally:
        from agents.config import ReadScopeViolation  # noqa: PLC0415

        observe_budget_exc = (
            observe_task.exception() if observe_task.done() else None
        )
        scope_hop_exit = (
            isinstance(primary_exc, (ReadScopeViolation, BudgetExceeded))
            or isinstance(observe_budget_exc, BudgetExceeded)
            or observe_task_read_scope_violation(observe_task) is not None
        )
        if scope_hop_exit:
            for task in (observe_task, wait_task):
                if not task.done():
                    task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    _await_cancelled_tasks(observe_task, wait_task),
                    timeout=CURSOR_HOP_CANCEL_DRAIN_SECONDS,
                )
        else:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    _await_cancelled_tasks(observe_task, wait_task),
                    timeout=CURSOR_STREAM_GETRUN_RPC_TIMEOUT_SECONDS + 5.0,
                )
        if primary_exc is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    persist_run_conversation(run, stream_log=ndjson_path),
                    timeout=10.0,
                )


def skip_conversation_persist_for_stream_log(stream_log: str | Path | None) -> bool:
    """Не писать ``conversation.json`` рядом с единым ``opsx/logs/.../<session>.log``."""
    if stream_log is None or not str(stream_log).strip():
        return True
    name = Path(stream_log).name
    return name.endswith(".log") and ".live." not in name


def conversation_path_from_stream_log(stream_log: str | Path) -> Path:
    """Соседний ``*.conversation.json`` для пути ``*.live.ndjson`` (делегат в ABCAgent)."""
    from agents.abc_agent import ABCAgent

    return ABCAgent.conversation_path_for_stream_log(stream_log)


async def persist_run_conversation(
    run: Any,
    *,
    stream_log: str | Path | None,
) -> Path | None:
    """Сохранить ``AsyncRun.conversation_json()`` для post-run анализа.

    Вызывается после успешного hop и при timeout (пока ``run`` и client ещё живы).
    Пропуск при ``OPSX_SKIP_CONVERSATION_PERSIST=1`` или пустом ``stream_log``.

    Returns:
        Путь записанного файла или ``None``, если сохранение не выполнялось.
    """
    if os.environ.get("OPSX_SKIP_CONVERSATION_PERSIST", "").strip() == "1":
        return None
    if skip_conversation_persist_for_stream_log(stream_log):
        return None
    if stream_log is None or not str(stream_log).strip():
        return None
    if not getattr(run, "id", ""):
        return None
    target = conversation_path_from_stream_log(stream_log)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = await run.conversation_json()
    except Exception as exc:
        target.write_text(
            json.dumps(
                {
                    "run_id": str(getattr(run, "id", "") or ""),
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return target
    if not raw.strip():
        return None
    target.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
    return target
