"""Сборка ``AgentToolParentContext`` из transport state."""

from __future__ import annotations

from typing import Any, Mapping

from agents.agent_tools.parent_context import AgentToolParentContext


def parent_context_from_state(
    state: Mapping[str, Any],
    *,
    parent_agent_key: str,
) -> AgentToolParentContext:
    """Построить immutable parent ctx (данные state, не registry)."""
    change_id = str(state.get("change_id") or "").strip()
    slice_id_raw = state.get("slice_id") or state.get("current_slice_id")
    slice_id = str(slice_id_raw).strip() if slice_id_raw else None
    repo = str(state.get("repository_path") or state.get("workspace") or "").strip()
    run_id = str(state.get("run_uuid") or state.get("workflow_execution_id") or "") or None
    parent_agent_id = str(state.get("agent_id") or "") or None
    return AgentToolParentContext(
        change_id=change_id,
        slice_id=slice_id,
        parent_agent_key=parent_agent_key,
        repository_path=repo,
        parent_run_id=run_id,
        parent_agent_id=parent_agent_id,
    )


def enrich_transport_with_agent_tools(
    transport: dict[str, Any],
    adapter: Any,
    *,
    parent_agent_key: str,
) -> dict[str, Any]:
    """Добавить ``agent_tool_parent_ctx`` если на adapter есть registry."""
    registry = getattr(adapter, "agent_tool_registry", None)
    if registry is None:
        return transport
    out = dict(transport)
    out["agent_tool_parent_ctx"] = parent_context_from_state(
        transport,
        parent_agent_key=parent_agent_key,
    )
    return out
