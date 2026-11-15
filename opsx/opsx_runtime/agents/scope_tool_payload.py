"""Извлечение ``ReadScopeViolation`` из soft MCP/NDJSON tool-result (не exception hop)."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from agents.config import ReadScopeViolation


def _iter_text_blobs(value: Any) -> Iterator[str]:
    """Обойти вложенный tool-result и выдать все строковые листья."""
    if isinstance(value, str):
        yield value
        return
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _iter_text_blobs(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_text_blobs(item)


def _violation_from_mapping(
    data: Mapping[str, Any],
    *,
    tool: str,
    agent_id: str | None,
    run_id: str | None,
) -> ReadScopeViolation | None:
    """Построить violation из распарсенного JSON-объекта tool-error."""
    code = str(data.get("code") or "").strip()
    rule = str(data.get("rule") or "").strip()
    if code != "READ_SCOPE_VIOLATION" or rule != "outside_restrict_allow":
        return None
    path = str(data.get("path") or "").strip()
    if not path:
        return None
    tool_name = str(tool or data.get("tool") or "read").strip().lower() or "read"
    return ReadScopeViolation(
        tool=tool_name,
        path=path,
        rule=rule,
        agent_id=agent_id,
        run_id=run_id,
    )


def read_scope_violation_from_tool_payload(
    *,
    tool: str,
    payload: Any,
    agent_id: str | None = None,
    run_id: str | None = None,
) -> ReadScopeViolation | None:
    """Вернуть ``outside_restrict_allow`` из soft tool-result, иначе ``None``."""
    if isinstance(payload, Mapping):
        direct = _violation_from_mapping(
            payload,
            tool=tool,
            agent_id=agent_id,
            run_id=run_id,
        )
        if direct is not None:
            return direct
    for blob in _iter_text_blobs(payload):
        text = blob.strip()
        if not text.startswith("{"):
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, Mapping):
            continue
        found = _violation_from_mapping(
            parsed,
            tool=tool,
            agent_id=agent_id,
            run_id=run_id,
        )
        if found is not None:
            return found
    return None


def read_scope_violation_from_stream_record(
    record: Mapping[str, Any],
    *,
    agent_id: str | None = None,
    run_id: str | None = None,
) -> ReadScopeViolation | None:
    """Извлечь soft violation из NDJSON ``tool_call`` / ``tool-call-completed``."""
    kind = str(record.get("kind") or "")
    tool = ""
    payload: Any = None
    if kind == "tool_call":
        tool = str(record.get("name") or "")
        payload = record.get("result")
        if payload is None:
            return None
    elif kind == "interaction":
        update = str(record.get("update_type") or "")
        if update != "tool-call-completed":
            return None
        tool_call = record.get("tool_call") or {}
        if not isinstance(tool_call, Mapping):
            return None
        tool = str(tool_call.get("name") or tool_call.get("toolName") or "")
        payload = tool_call.get("result")
    else:
        return None
    return read_scope_violation_from_tool_payload(
        tool=tool,
        payload=payload,
        agent_id=agent_id,
        run_id=run_id,
    )
