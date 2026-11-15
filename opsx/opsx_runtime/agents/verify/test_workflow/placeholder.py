"""Placeholder test workflow: фиксирует «not implemented» и возвращает outcome в dispatcher."""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.dispatcher.agent import dispatcher_record_outcome


async def test_workflow_placeholder_run(state: GraphState) -> dict[str, Any]:
    """Записать terminal placeholder для pytest/WF_PYTEST_* без subgraph."""
    item = state.get("dispatcher_current_item")
    finding_id = (item or {}).get("finding_id") if isinstance(item, dict) else None
    outcome = {
        "schema_version": "1.0",
        "agent": "test_workflow_placeholder",
        "finding_id": finding_id,
        "status": "NOT_IMPLEMENTED",
        "message": "test workflow not implemented yet",
    }
    advance = await dispatcher_record_outcome(
        state,
        workflow_kind="test",
        outcome=outcome,
    )
    return {
        **advance,
        "test_workflow_placeholder_result": outcome,
    }
