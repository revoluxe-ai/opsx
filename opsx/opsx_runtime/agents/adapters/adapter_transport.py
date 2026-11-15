"""Neutral GraphState → adapter transport payload (no provider imports)."""

from __future__ import annotations

from typing import Any, Mapping


def adapter_transport_state(state: Mapping[str, Any], **overrides: Any) -> dict[str, Any]:
    """Slice GraphState for adapter transport (workspace, model, stream, diagnostics).

    Does not copy ``agent_id`` from GraphState — resume only via
    ``ABCAdapter.run(resume_agent_id=...)``.
    """
    transport: dict[str, Any] = {
        "repository_path": str(state["repository_path"]),
        "workspace": str(state.get("workspace") or state["repository_path"]),
    }
    if state.get("model") is not None:
        transport["model"] = state["model"]
    for key in ("change_id", "run_uuid", "isolated_workspace", "run_dialog_agent"):
        if state.get(key) is not None:
            transport[key] = state[key]
    for key in (
        "quality_gate_diagnostics",
        "quality_gate_raw",
        "analysis_result",
        "repair_order_result",
        "dispatcher_current_item",
    ):
        if key in state and state[key] is not None:
            transport[key] = state[key]
    transport.update(overrides)
    return transport
