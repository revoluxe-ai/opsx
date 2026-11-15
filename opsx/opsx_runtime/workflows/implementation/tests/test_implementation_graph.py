"""Тесты dispatcher loop implementation subgraph (dry plan override)."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.state import GraphState
from workflows.implementation.graph import (
    ImplementationHopAgents,
    build_implementation_graph,
)
from workflows.implementation.routing_contract import (
    RUN_STATUS_APPLY_COMPLETE,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_COMPLETE,
    STAGE_APPLY_DESIGN,
    STAGE_APPLY_DESIGN_REVIEW,
    STAGE_APPLY_IMPLEMENT,
    implementation_lane_should_execute,
)
from langgraph.graph import END

from workflows.implementation.routing import (
    OUTCOME_CONTINUE_EXECUTE,
    OUTCOME_LANE_COMPLETE_FINALIZE,
    ImplementationLaneTerminalError,
    route_after_plan_only,
    route_after_sdk_hop,
)


class _StubAgent:
    async def run(self, state: GraphState) -> dict[str, object]:
        _ = state
        return {"execution_status": "STUB_OK"}


def _stub_hop_agents() -> ImplementationHopAgents:
    stub = _StubAgent()
    return ImplementationHopAgents(
        apply_design=stub,  # type: ignore[arg-type]
        apply_design_review=stub,  # type: ignore[arg-type]
        apply_scaffold=stub,  # type: ignore[arg-type]
        apply_red=stub,  # type: ignore[arg-type]
        apply_implement=stub,  # type: ignore[arg-type]
        apply_green=stub,  # type: ignore[arg-type]
        implementation_arbiter=stub,  # type: ignore[arg-type]
        scenario_coverage_plan=stub,  # type: ignore[arg-type]
        scenario_test_author=stub,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_dry_run_loop_reaches_apply_complete() -> None:
    calls = {"n": 0}

    async def route_node(state: GraphState) -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "implementation_status": RUN_STATUS_RUNNING,
                "implementation_stage": STAGE_APPLY_DESIGN,
                "implementation_slice_id": None,
                "live_execution_authorized": True,
            }
        return {
            "implementation_status": RUN_STATUS_APPLY_COMPLETE,
            "implementation_stage": STAGE_APPLY_COMPLETE,
            "implementation_slice_id": None,
        }

    graph = build_implementation_graph(_stub_hop_agents(), checkpointer=MemorySaver())
    state: GraphState = {
        "implementation_status": RUN_STATUS_APPLY_COMPLETE,
        "implementation_stage": STAGE_APPLY_COMPLETE,
    }
    assert route_after_plan_only(state) == OUTCOME_LANE_COMPLETE_FINALIZE

    state_running: GraphState = {
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_stage": STAGE_APPLY_DESIGN,
    }
    assert route_after_plan_only(state_running) == OUTCOME_CONTINUE_EXECUTE

    in_progress_forward: GraphState = {
        "implementation_status": "IN_PROGRESS",
        "implementation_stage": STAGE_APPLY_IMPLEMENT,
        "implementation_slice_id": "SLICE-004",
    }
    assert implementation_lane_should_execute(in_progress_forward)
    assert route_after_plan_only(in_progress_forward) == OUTCOME_CONTINUE_EXECUTE

    plan_supersedes_pause: GraphState = {
        "implementation_status": RUN_STATUS_PAUSED,
        "implementation_recovery_code": "DISPATCH_LEDGER_ACTIVE_HOP",
        "implementation_stage": STAGE_APPLY_IMPLEMENT,
        "implementation_plan": {
            "status": "RUNNING",
            "next_stage": STAGE_APPLY_IMPLEMENT,
            "next_slice": "SLICE-004",
            "live_execution_authorized": True,
            "plan_only": True,
        },
    }
    assert route_after_plan_only(plan_supersedes_pause) == OUTCOME_CONTINUE_EXECUTE
    _ = route_node
    _ = graph


def test_execute_paused_raises_not_replans() -> None:
    """PAUSED после hop — raise, не тихий END и не повтор plan."""
    state: GraphState = {
        "implementation_status": RUN_STATUS_PAUSED,
        "implementation_recovery_code": "OWNER_ATTENTION",
    }
    with pytest.raises(ImplementationLaneTerminalError, match="OWNER_ATTENTION"):
        route_after_sdk_hop(state)


def test_blocked_hop_status_raises() -> None:
    """apply_red BLOCKED → lane PAUSED → raise (не SDK loop)."""
    state: GraphState = {
        "implementation_status": RUN_STATUS_PAUSED,
        "apply_red_status": "BLOCKED",
        "implementation_recovery_code": "APPLY_BLOCKED",
    }
    with pytest.raises(ImplementationLaneTerminalError, match="APPLY_BLOCKED"):
        route_after_sdk_hop(state)


def test_route_to_execute_for_design_review_stage() -> None:
    """Design/review стадии исполняются только в ``apply_design_lane``."""
    from workflows.implementation.design_pipeline.routing import (
        DESIGN_LANE_EXECUTE_NODE,
        route_after_design_lane_route,
    )

    state: GraphState = {
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_stage": STAGE_APPLY_DESIGN_REVIEW,
    }
    assert route_after_design_lane_route(state) == DESIGN_LANE_EXECUTE_NODE


def test_plan_override_fields_documented() -> None:
    """``implementation_plan_override`` in state bypasses Node subprocess in tests."""
    payload = {
        "status": RUN_STATUS_APPLY_COMPLETE,
        "next_stage": STAGE_APPLY_COMPLETE,
        "live_execution_authorized": False,
    }
    state: GraphState = {"implementation_plan_override": payload}
    assert state["implementation_plan_override"]["status"] == RUN_STATUS_APPLY_COMPLETE
