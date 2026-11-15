"""Минимальный probe Cursor SDK: ``run.wait()`` параллельно с ``run.observe()``.

Диагностика ok_probe без idle/recovery логики production-адаптера
``cursor_sdk_stream.py``. Каждое событие пишется в NDJSON; на выходе —
накопленные thinking/assistant тексты и счётчики по kind.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from cursor_sdk.asyncio import AsyncAgent, AsyncClient
from cursor_sdk.types import RunResult, RunStreamEvent

from .cursor_sdk_stream import (
    interaction_update_record,
    sdk_message_record,
)

_JSON_SNIPPET_RE = re.compile(r"\{[^{}]*\"answer\"[^{}]*\}", re.DOTALL)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_ndjson(path: Path | None, label: str, record: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {"ts": _utc_now(), "label": label, **record},
        ensure_ascii=False,
        default=str,
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


def _event_summary(event: RunStreamEvent) -> dict[str, Any]:
    """Краткая сводка полей wire-события для NDJSON."""
    summary: dict[str, Any] = {"wire_kind": event.kind, "offset": event.offset}
    if event.sdk_message is not None:
        summary["sdk_type"] = getattr(event.sdk_message, "type", None)
    if event.interaction_update is not None:
        summary["update_type"] = getattr(event.interaction_update, "type", None)
    if event.done:
        summary["done_status"] = str((event.done or {}).get("status") or "")
    return summary


def _extract_texts_from_record(record: dict[str, Any]) -> tuple[str, str]:
    """Вернуть (thinking_delta, assistant_delta) из NDJSON-записи события."""
    kind = str(record.get("kind") or "")
    thinking = ""
    assistant = ""
    if kind == "thinking":
        thinking = str(record.get("text") or "")
    elif kind == "assistant":
        assistant = str(record.get("text") or "")
    elif kind == "interaction":
        update_type = str(record.get("update_type") or "")
        text = str(record.get("text") or "")
        if update_type == "thinking-delta":
            thinking = text
        elif update_type == "text-delta":
            assistant = text
    elif kind == "usage":
        pass
    else:
        payload = record.get("payload")
        if isinstance(payload, Mapping):
            for key in ("text", "reasoning_content", "reasoning"):
                raw = payload.get(key)
                if raw:
                    thinking += str(raw)
    return thinking, assistant


async def _cancel_run(run: Any) -> None:
    run_id = str(getattr(run, "id", "") or "")
    client = getattr(run, "client", None)
    if not run_id or client is None:
        return
    cancel = getattr(client, "cancel_run", None)
    if callable(cancel):
        await cancel(run_id)


async def _wait_with_minimal_observe(
    run: Any,
    *,
    timeout_seconds: float,
    stream_log: Path | None,
    stream_label: str,
) -> dict[str, Any]:
    """Ядро: ``create_task(run.wait())`` + цикл ``observe().__anext__()``."""
    thinking_parts: list[str] = []
    assistant_parts: list[str] = []
    json_snippets: list[str] = []
    event_counts: Counter[str] = Counter()
    usage_record: dict[str, Any] | None = None
    meta_logged = False

    observe = getattr(run, "observe", None)
    observe_iter = observe().__aiter__() if callable(observe) else None

    def _consume_event(event: RunStreamEvent) -> None:
        nonlocal meta_logged, usage_record
        if not meta_logged and getattr(run, "id", "") and getattr(run, "agent_id", ""):
            _append_ndjson(
                stream_log,
                stream_label,
                {
                    "kind": "run_meta",
                    "run_id": str(run.id),
                    "agent_id": str(run.agent_id),
                },
            )
            meta_logged = True

        event_counts[str(event.kind or "event")] += 1
        record: dict[str, Any]
        if event.sdk_message is not None:
            record = sdk_message_record(event.sdk_message)
        elif event.interaction_update is not None:
            record = interaction_update_record(event.interaction_update)
        elif event.step is not None:
            record = {"kind": "step", "payload": str(event.step)}
        elif event.kind == "done":
            record = {
                "kind": "terminal",
                "wire_kind": "done",
                "status": str((event.done or {}).get("status") or "done"),
            }
        elif event.kind == "result":
            record = {"kind": "terminal", "wire_kind": "result"}
        else:
            record = {"kind": event.kind or "unknown", "summary": _event_summary(event)}

        record["summary"] = _event_summary(event)
        _append_ndjson(stream_log, stream_label, record)

        thinking_delta, assistant_delta = _extract_texts_from_record(record)
        if thinking_delta:
            thinking_parts.append(thinking_delta)
            for match in _JSON_SNIPPET_RE.finditer(thinking_delta):
                json_snippets.append(match.group(0))
        if assistant_delta:
            assistant_parts.append(assistant_delta)
            for match in _JSON_SNIPPET_RE.finditer(assistant_delta):
                json_snippets.append(match.group(0))
        if record.get("kind") == "usage" and isinstance(record.get("usage"), Mapping):
            usage_record = dict(record["usage"])

    async def _observe_sidecar(wait_task: asyncio.Task[Any]) -> None:
        if observe_iter is None:
            return
        while not wait_task.done():
            try:
                event = await asyncio.wait_for(observe_iter.__anext__(), timeout=0.25)
            except asyncio.TimeoutError:
                continue
            except StopAsyncIteration:
                break
            _consume_event(event)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    wait_task = asyncio.create_task(run.wait())
    observe_task = asyncio.create_task(_observe_sidecar(wait_task))
    wait_status = "ok"
    terminal: RunResult | None = None
    try:
        while not wait_task.done():
            if loop.time() >= deadline:
                wait_status = "timeout"
                await _cancel_run(run)
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    terminal = await asyncio.wait_for(wait_task, timeout=30.0)
                message = f"minimal probe exceeded {timeout_seconds}s"
                _append_ndjson(
                    stream_log,
                    stream_label,
                    {"kind": "timeout", "message": message},
                )
                raise asyncio.TimeoutError(message)
            await asyncio.sleep(0.05)
        terminal = wait_task.result()
    finally:
        observe_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await observe_task
        if not wait_task.done():
            wait_task.cancel()

    run_usage = getattr(run, "usage", None)
    usage_out: dict[str, Any] | None = usage_record
    if run_usage is not None:
        usage_out = {
            "input_tokens": getattr(run_usage, "input_tokens", None),
            "output_tokens": getattr(run_usage, "output_tokens", None),
            "reasoning_tokens": getattr(run_usage, "reasoning_tokens", None),
            "total_tokens": getattr(run_usage, "total_tokens", None),
        }

    thinking_text = "".join(thinking_parts)
    assistant_text = "".join(assistant_parts)
    return {
        "wait_status": wait_status,
        "wait_result": terminal,
        "run_id": str(getattr(run, "id", "") or ""),
        "agent_id": str(getattr(run, "agent_id", "") or ""),
        "usage": usage_out,
        "thinking_text": thinking_text,
        "assistant_text": assistant_text,
        "wait_result_text": str(getattr(terminal, "result", "") or ""),
        "json_snippets": json_snippets,
        "event_counts": dict(event_counts),
    }


async def run_minimal_probe(
    client: AsyncClient,
    prompt: str,
    *,
    model: Mapping[str, Any],
    local: Mapping[str, Any],
    api_key: str,
    tools: Sequence[str] = (),
    agent_id: str | None = None,
    stream_log: Path | None = None,
    stream_label: str = "minimal-probe",
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Один SDK hop: create/resume agent, send, minimal observe+wait, NDJSON log.

    Args:
        client: Уже открытый ``AsyncClient`` (bridge).
        prompt: Текст user-turn.
        model: Выбор модели (как ``state['model']`` у ``CursorSDKAdapter``).
        local: ``agent_local_options`` для ``options['local']``.
        api_key: Cursor API key.
        tools: Разрешённые инструменты (ok_probe — пустой tuple).
        agent_id: Resume существующего агента или ``None`` для create.
        stream_log: Путь ``*.live.ndjson`` или ``None``.
        stream_label: Метка строк NDJSON.
        timeout_seconds: Wall-clock бюджет hop; по истечении — ``CancelRun``.

    Returns:
        Словарь с ``usage``, ``wait_result``, накопленными текстами и ``event_counts``.

    Raises:
        asyncio.TimeoutError: hop не завершился до ``timeout_seconds``.
        ValueError: пустой prompt или некорректная model selection.
    """
    if not prompt.strip():
        raise ValueError("prompt must not be empty")
    if not isinstance(model, Mapping) or not model.get("id"):
        raise ValueError("model must be a Cursor SDK model selection with id")

    options: dict[str, Any] = {
        "apiKey": api_key,
        "model": dict(model),
        "local": dict(local),
        "tools": list(tools),
    }
    if agent_id:
        agent = await AsyncAgent.resume(str(agent_id), options, client=client)
    else:
        agent = await AsyncAgent.create(options, client=client)

    run = await agent.send(prompt, {"tools": list(tools)})
    outcome = await _wait_with_minimal_observe(
        run,
        timeout_seconds=timeout_seconds,
        stream_log=stream_log,
        stream_label=stream_label,
    )
    outcome["prompt_chars"] = len(prompt)
    return outcome
