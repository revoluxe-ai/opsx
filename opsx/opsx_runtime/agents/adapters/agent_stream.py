"""Neutral live-log stream labels for adapter hops."""

from __future__ import annotations

import logging
from typing import Any, Mapping

_FAULT_LIVE_RECORD_KINDS = frozenset({"timeout"})


def live_stream_log_level(record: Mapping[str, Any]) -> int:
    """Уровень std logging для live-log: fault=ERROR, диагностика=DEBUG, ход=INFO."""
    kind = str(record.get("kind") or "")
    if kind in {
        "stream_unknown",
        "wait_state",
        "get_run_poll",
        "observe_resume",
        "observe_signal",
    }:
        return logging.DEBUG
    if kind in _FAULT_LIVE_RECORD_KINDS:
        return logging.ERROR
    if kind == "stall":
        return logging.ERROR
    if kind == "terminal":
        status = str(record.get("status") or "")
        if status in {"error", "cancelled", "expired"}:
            return logging.ERROR
    return logging.INFO

_CHECK_WORKFLOW_AGENT_PREFIX: dict[str, str] = {
    "project_bug_hunt": "project_bug",
    "bugbot": "bugbot",
    "security_review": "security",
}

_STREAM_AGENT_DISPLAY: dict[str, str] = {
    "project_bug_hunt": "project_bug",
    "security_review": "security",
}

_STATIC_REPAIR_STREAM_AGENTS = frozenset(
    {
        "group_error",
        "repair_order",
        "diagnostic",
        "static_triage",
        "typing_repair",
        "test_triage",
        "test_repair",
    },
)

_SPLIT_APPLY_SLICE_STREAM_AGENTS = frozenset(
    {
        "apply_scaffold",
        "apply_red",
        "apply_implement",
        "apply_green",
    },
)

_AGENT_RUN_SCOPE_STATE_KEYS: dict[str, tuple[str, ...]] = {
    "group_error": ("group_error_stream_label",),
    "repair_order": ("repair_order_stream_label",),
    "diagnostic": ("diagnostic_stream_label",),
}


def stream_model_slug(state: Mapping[str, Any]) -> str | None:
    """Model slug from ``state['model']``."""
    model = state.get("model")
    if isinstance(model, Mapping):
        model_id = model.get("id")
        if model_id:
            return str(model_id)
    return None


def stream_round_for_hop(state: Mapping[str, Any], agent_name: str) -> int | None:
    """Round index for the current hop (check loop / outer / green restart)."""
    check_prefix = _CHECK_WORKFLOW_AGENT_PREFIX.get(agent_name)
    if check_prefix is not None:
        loop_key = f"{check_prefix}_check_loop_pass"
        if loop_key in state:
            return int(state.get(loop_key) or 0) + 1
        return None
    if agent_name == "impl_verify":
        return int(state.get("verify_green_restart_count") or 0) + 1
    if agent_name == "scenario_coverage_verify":
        return int(state.get("verify_green_restart_count") or 0) + 1
    if agent_name in _STATIC_REPAIR_STREAM_AGENTS:
        outer = state.get("verify_outer_iteration")
        if isinstance(outer, int) and outer > 0:
            return outer
    return None


def _run_scope_from_state(state: Mapping[str, Any], agent_name: str) -> str | None:
    """Run id for live-log prefix, without ``|``."""
    keys = _AGENT_RUN_SCOPE_STATE_KEYS.get(agent_name, ("stream_label",))
    for key in keys:
        raw = state.get(key)
        if isinstance(raw, str):
            scope = raw.strip()
            if scope and "|" not in scope:
                return scope
    return None


def compose_agent_stream_label(
    state: Mapping[str, Any],
    agent_name: str,
    *,
    run_scope: str | None = None,
) -> str:
    """Build live-log label: ``run|agent [round=N] [model=slug]``."""
    display = _STREAM_AGENT_DISPLAY.get(agent_name, agent_name.strip() or "agent")
    parts: list[str] = [display]
    if agent_name in _SPLIT_APPLY_SLICE_STREAM_AGENTS:
        raw_slice = state.get("implementation_slice_id")
        if isinstance(raw_slice, str):
            slice_id = raw_slice.strip()
            if slice_id:
                parts.append(f"slice={slice_id}")
    round_n = stream_round_for_hop(state, agent_name)
    if round_n is not None:
        parts.append(f"round={round_n}")
    model_slug = stream_model_slug(state)
    if model_slug:
        parts.append(f"model={model_slug}")
    agent_meta = " ".join(parts)
    scope = run_scope if run_scope is not None else _run_scope_from_state(state, agent_name)
    if scope:
        return f"{scope}|{agent_meta}"
    return agent_meta
