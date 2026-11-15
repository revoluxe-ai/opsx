"""Артефакты диалогов OPSX-агентов для post-run анализа.

Пишет per-agent каталоги и общий ``run.log`` под
``opsx/runs/dialogs/<change_id>/<run_uuid>/``.
Legacy ``*.conversation.json`` рядом с ``*.live.ndjson`` не заменяет — dual-write.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import re
import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import AgentResult
from agents.abc_agent import ABCAgent

__all__ = [
    "agent_run_dialog_dir",
    "infer_agent_folder_name",
    "persist_run_dialog_artifacts",
    "resolve_run_dialog_ids",
    "run_dialog_launch_dir",
    "shared_run_log_path",
]

_SHARED_LOG_LOCK = threading.Lock()

_STREAM_STEM_AGENT: tuple[tuple[str, str], ...] = (
    ("impl-verify-", "impl_verify"),
    ("scenario-coverage-", "scenario_coverage_verify"),
    ("group-error-", "group_error"),
    ("repair-order-", "repair_order"),
    ("diagnostic-", "diagnostic"),
    ("static-triage-", "static_triage"),
    ("typing-repair-", "typing_repair"),
    ("test-triage-", "test_triage"),
    ("test-repair-", "test_repair"),
    ("apply-", "apply"),
    ("bugbot", "bugbot"),
    ("ok-probe-minimal-", "ok_probe"),
    ("ok-probe-health-", "ok_probe"),
)

_SESSION_SUFFIX = re.compile(r"^[0-9a-f]{8,32}$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_segment(raw: str, *, fallback: str = "agent") -> str:
    """Filesystem-safe slug для каталога агента или change_id."""
    text = (raw or "").strip().lower()
    if not text:
        return fallback
    text = text.replace("\\", "-").replace("/", "-").replace("|", "-")
    text = re.sub(r"[^a-z0-9._+-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or fallback


def _repo_root(state: Mapping[str, Any]) -> Path:
    from workflows.repo_paths import resolve_opsx_repository_root

    for key in ("repository_path", "workspace"):
        raw = state.get(key)
        if raw and str(raw).strip():
            candidate = Path(str(raw))
            try:
                return resolve_opsx_repository_root(candidate)
            except ValueError:
                return candidate.resolve()
    return Path.cwd().resolve()


def run_dialogs_root(repo_root: Path) -> Path:
    """Корень дерева run_dialogs относительно репозитория."""
    return resource_path(repo_root, "opsx", "runs", "dialogs")


def resolve_run_dialog_ids(
    state: Mapping[str, Any],
    stream_log: str | Path | None,
) -> tuple[str, str]:
    """Вернуть ``(change_id, run_uuid)`` с безопасными fallback."""
    change_id = str(state.get("change_id") or "").strip()
    if not change_id:
        change_id = "unknown-change"
    change_id = _sanitize_segment(change_id, fallback="unknown-change")

    run_uuid = str(state.get("run_uuid") or state.get("session_id") or "").strip()
    if not run_uuid and stream_log:
        stem = Path(str(stream_log)).stem
        for prefix, _ in _STREAM_STEM_AGENT:
            if stem.startswith(prefix):
                suffix = stem[len(prefix) :]
                if _SESSION_SUFFIX.match(suffix):
                    run_uuid = suffix
                break
        if not run_uuid:
            match = re.search(r"-([0-9a-f]{8,32})$", stem)
            if match:
                run_uuid = match.group(1)
    if not run_uuid:
        run_uuid = uuid.uuid4().hex[:12]
    run_uuid = _sanitize_segment(run_uuid, fallback=uuid.uuid4().hex[:12])
    return change_id, run_uuid


def infer_agent_folder_name(
    *,
    state: Mapping[str, Any],
    stream_label: str,
    stream_log: str | Path | None,
) -> str:
    """Имя каталога агента из stream label / имени live.ndjson."""
    explicit = state.get("run_dialog_agent")
    if explicit and str(explicit).strip():
        return _sanitize_segment(str(explicit))

    label = (stream_label or "").strip()
    if "|" in label:
        agent_part = label.split("|", 1)[1].strip()
        if agent_part:
            token = agent_part.split()[0]
            return _sanitize_segment(token)

    if stream_log:
        stem = Path(str(stream_log)).stem
        for prefix, agent in _STREAM_STEM_AGENT:
            if stem.startswith(prefix):
                return agent
        if stem.endswith(".live"):
            stem = stem[: -len(".live")]
        base = re.sub(r"-[0-9a-f]{8,32}$", "", stem)
        if base:
            return _sanitize_segment(base.replace("-", "_"))

    if label:
        return _sanitize_segment(label.split()[0])
    return "agent"


def run_dialog_launch_dir(
    repo_root: Path,
    change_id: str,
    run_uuid: str,
) -> Path:
    """Каталог одного launch: ``.../run_dialogs/<change_id>/<run_uuid>/``."""
    return run_dialogs_root(repo_root) / change_id / run_uuid


def agent_run_dialog_dir(
    repo_root: Path,
    change_id: str,
    run_uuid: str,
    agent_name: str,
) -> Path:
    """Per-agent каталог артефактов внутри launch."""
    return run_dialog_launch_dir(repo_root, change_id, run_uuid) / agent_name


def shared_run_log_path(
    repo_root: Path,
    change_id: str,
    run_uuid: str,
) -> Path:
    """Общий хронологический лог всех агентов launch."""
    return run_dialog_launch_dir(repo_root, change_id, run_uuid) / "run.log"


def _model_id_from_state(state: Mapping[str, Any]) -> str | None:
    model = state.get("model")
    if isinstance(model, Mapping):
        mid = model.get("id")
        if mid:
            return str(mid)
    return None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _json_preview(value: Any, limit: int = 240) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        raw = str(value)
    return _truncate(raw, limit)


def _conversation_turn_steps(conversation: Any) -> list[tuple[str, dict[str, Any]]]:
    """Плоский список ``(step_type, step_dict)`` из SDK conversation JSON."""
    steps_out: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(conversation, list):
        return steps_out
    for item in conversation:
        if not isinstance(item, dict):
            continue
        turn = item.get("turn")
        if not isinstance(turn, dict):
            continue
        steps = turn.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if isinstance(step, dict):
                steps_out.append((str(step.get("type") or "step"), step))
    return steps_out


def _readable_lines_from_conversation(
    conversation: Any,
    *,
    max_assistant: int = 4000,
    max_tool_args: int = 240,
    max_tool_result: int = 400,
) -> list[str]:
    """Строки для ``conversation.md`` и ``run.log``."""
    lines: list[str] = []
    if isinstance(conversation, dict) and conversation.get("error"):
        lines.append(f"**error:** {conversation.get('error')}")
        return lines
    if not isinstance(conversation, list):
        lines.append(_truncate(json.dumps(conversation, ensure_ascii=False), max_assistant))
        return lines

    turn_idx = 0
    for item in conversation:
        if not isinstance(item, dict):
            continue
        turn = item.get("turn")
        if not isinstance(turn, dict):
            continue
        steps = turn.get("steps")
        if not isinstance(steps, list) or not steps:
            continue
        turn_idx += 1
        lines.append(f"### Turn {turn_idx}")
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_type = str(step.get("type") or "")
            if step_type == "assistantMessage":
                message = step.get("message")
                text = ""
                if isinstance(message, dict):
                    text = str(message.get("text") or "").strip()
                if text:
                    lines.append(f"- **assistant:** {_truncate(text, max_assistant)}")
            elif step_type in {"toolCall", "tool_call"}:
                message = step.get("message") or step.get("toolCall") or step
                name = "?"
                args: Any = None
                result: Any = None
                if isinstance(message, dict):
                    name = str(
                        message.get("name")
                        or message.get("toolName")
                        or message.get("tool")
                        or "?"
                    )
                    args = message.get("args") or message.get("arguments")
                    result = message.get("result")
                lines.append(
                    f"- **tool** `{name}` args: `{_json_preview(args, max_tool_args)}`"
                )
                if result is not None:
                    preview = _json_preview(result, max_tool_result)
                    lines.append(
                        f"  - result: {preview} _(truncated if long)_"
                    )
            elif step_type == "userMessage":
                message = step.get("message")
                text = ""
                if isinstance(message, dict):
                    text = str(message.get("text") or "").strip()
                if text:
                    lines.append(f"- **user:** {_truncate(text, max_assistant)}")
            else:
                lines.append(f"- _{step_type}_")
    return lines


def conversation_to_markdown(conversation: Any) -> str:
    """Человекочитаемый markdown полного диалога."""
    body = _readable_lines_from_conversation(conversation)
    if not body:
        return "_empty conversation_\n"
    return "\n".join(body) + "\n"


def _usage_summary_line(agent_result: AgentResult | None) -> str:
    if agent_result is None or agent_result.usage is None:
        return "--- tokens: unavailable"
    u = agent_result.usage
    parts = [
        f"input={u.get('input_tokens')}",
        f"output={u.get('output_tokens')}",
        f"cache_read={u.get('cache_read_tokens')}",
        f"cache_write={u.get('cache_write_tokens')}",
        f"total={u.get('total_tokens')}",
    ]
    line = "--- tokens: " + " ".join(parts)
    line += f" | usage_source={agent_result.usage_source}"
    line += f" | cost_status={agent_result.cost_status}"
    if agent_result.cost:
        line += f" | cost={json.dumps(agent_result.cost, ensure_ascii=False)}"
    if agent_result.billing_error:
        line += f" | billing_error={_truncate(agent_result.billing_error, 200)}"
    return line


def _append_shared_run_log(
    log_path: Path,
    *,
    header: str,
    body_lines: Sequence[str],
    footer: str,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    block_lines = [
        "",
        "=" * 80,
        header,
        "-" * 80,
        *body_lines,
        footer,
        "=" * 80,
        "",
    ]
    text = "\n".join(block_lines)
    with _SHARED_LOG_LOCK:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()


def _load_conversation_payload(legacy_path: Path) -> Any:
    if not legacy_path.is_file():
        return None
    raw = legacy_path.read_text(encoding="utf-8")
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "invalid_json", "raw_preview": _truncate(raw, 500)}


def persist_run_dialog_artifacts(
    *,
    state: Mapping[str, Any],
    stream_log: str | Path | None,
    stream_label: str,
    agent_result: AgentResult | None = None,
    inbox_capture_session: Any | None = None,
) -> Path | None:
    """Записать meta/conversation в run_dialogs и дописать общий ``run.log``.

    Args:
        state: GraphState / adapter state (``change_id``, ``run_uuid``, ``model``).
        stream_log: Путь ``*.live.ndjson`` (legacy conversation рядом).
        stream_label: Метка hop для infer имени агента.
        agent_result: Итог SDK hop (usage/cost/run_id), если уже собран.

    Returns:
        Каталог агента или ``None``, если persist отключён / нет stream_log.
    """
    import os

    if os.environ.get("OPSX_SKIP_CONVERSATION_PERSIST", "").strip() == "1":
        return None
    if stream_log is None or not str(stream_log).strip():
        return None

    from opsx_recording.adapter_hooks import (
        begin_adapter_inbox_capture,
        finish_adapter_inbox_capture,
    )
    from run_dialog_immutable import write_immutable_text

    repo_root = _repo_root(state)
    change_id, run_uuid = resolve_run_dialog_ids(state, stream_log)
    agent_name = infer_agent_folder_name(
        state=state,
        stream_label=stream_label,
        stream_log=stream_log,
    )
    # rebase synthesis (d749a07e + dev): scope in adapter; legacy callers без scope
    if inbox_capture_session is not None:
        capture_session = inbox_capture_session
    else:
        capture_session = begin_adapter_inbox_capture(
            state,
            stream_label=stream_label,
            stream_log=stream_log,
            resume_run_id=agent_result.run_id if agent_result else None,
        )
    agent_dir = agent_run_dialog_dir(repo_root, change_id, run_uuid, agent_name)
    agent_dir.mkdir(parents=True, exist_ok=True)

    revision_id = (
        str(agent_result.run_id).strip()
        if agent_result and agent_result.run_id
        else _utc_now_iso()
    )

    legacy_conv = ABCAgent.conversation_path_for_stream_log(stream_log)
    conversation = _load_conversation_payload(legacy_conv)

    conv_json_path = agent_dir / "conversation.json"
    if legacy_conv.is_file():
        write_immutable_text(
            conv_json_path,
            legacy_conv.read_text(encoding="utf-8"),
            revision_id=revision_id,
        )
        # synthesis: immutable canonical может остаться от hop-1; inbox — актуальный legacy stream
        conv_source = legacy_conv
    elif conversation is not None:
        write_immutable_text(
            conv_json_path,
            json.dumps(conversation, ensure_ascii=False, indent=2) + "\n",
            revision_id=revision_id,
        )
        conv_source = conv_json_path
    else:
        conv_source = conv_json_path

    md_path = agent_dir / "conversation.md"
    md_text = conversation_to_markdown(conversation)
    write_immutable_text(
        md_path,
        md_text,
        revision_id=revision_id,
    )
    md_source = md_path

    model_id = _model_id_from_state(state)
    finished_at = _utc_now_iso()
    meta: dict[str, Any] = {
        "change_id": change_id,
        "run_uuid": run_uuid,
        "agent": agent_name,
        "model_id": model_id,
        "usage": agent_result.usage if agent_result else None,
        "cost": agent_result.cost if agent_result else None,
        "cost_status": agent_result.cost_status if agent_result else None,
        "usage_source": agent_result.usage_source if agent_result else None,
        "billing_error": agent_result.billing_error if agent_result else None,
        "agent_id": agent_result.agent_id if agent_result else state.get("agent_id"),
        "sdk_run_id": agent_result.run_id if agent_result else None,
        "sdk_status": agent_result.status if agent_result else None,
        "finished_at": finished_at,
        "legacy_stream_log": str(Path(stream_log).resolve()),
        "legacy_conversation_json": str(legacy_conv.resolve())
        if legacy_conv.is_file()
        else None,
        "shared_run_log": str(
            shared_run_log_path(repo_root, change_id, run_uuid).resolve()
        ),
        "files": {
            "meta.json": "meta.json",
            "conversation_md": "conversation.md",
            "conversation_json": "conversation.json",
        },
    }
    meta_path = agent_dir / "meta.json"
    meta_text = json.dumps(meta, ensure_ascii=False, indent=2) + "\n"
    write_immutable_text(meta_path, meta_text, revision_id=revision_id)
    meta_source = meta_path
    run_dialog_artifact_paths = {
        "conversation.json": conv_source,
        "conversation.md": md_source,
        "meta.json": meta_source,
    }

    shared_log = shared_run_log_path(repo_root, change_id, run_uuid)
    header = (
        f"{finished_at} | agent={agent_name}"
        + (f" | model={model_id}" if model_id else "")
    )
    body = _readable_lines_from_conversation(
        conversation,
        max_assistant=2000,
        max_tool_args=180,
        max_tool_result=280,
    )
    if not body:
        body = ["_(no conversation turns)_"]
    footer = _usage_summary_line(agent_result)
    _append_shared_run_log(
        shared_log,
        header=header,
        body_lines=body,
        footer=footer,
    )
    _ = finish_adapter_inbox_capture(
        capture_session,
        state=state,
        stream_log=stream_log,
        agent_result=agent_result,
        run_dialog_agent_dir=agent_dir,
        run_dialog_artifact_paths=run_dialog_artifact_paths,
    )
    return agent_dir
