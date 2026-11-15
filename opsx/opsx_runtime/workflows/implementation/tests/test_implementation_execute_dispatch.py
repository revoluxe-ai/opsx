"""Execute hop: dispatch gate PAUSE must not fake a completed hop."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.state import GraphState
from workflows.implementation.code_pipeline.graph import (
    code_lane_agents_from_hop_agents,
    implementation_execute_hop_node,
)
from workflows.implementation.graph import ImplementationHopAgents
from workflows.implementation.routing_contract import (
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_IMPLEMENT,
)
from workflows.implementation.adapter_run_reconcile import Pause

_LEGACY_PLAN_ROUTE_NODE = "implementation_route"  # pre-split graph; см. checkpoint_resume


class _StubImplementAgent:
    async def run(self, state: GraphState) -> dict[str, object]:
        _ = state
        pytest.fail("mutating agent must not run when dispatch gate pauses")


def _hop_agents() -> ImplementationHopAgents:
    stub = _StubImplementAgent()
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
async def test_execute_hop_dispatch_pause_sets_last_hop_and_skips_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-agent dispatch PAUSE не должен писать ``implementation_last_hop``."""
    (tmp_path / "opsx").mkdir()

    async def _skip_plan(_state: GraphState) -> dict[str, object]:
        return {}

    monkeypatch.setattr(
        "workflows.implementation.code_pipeline.graph.run_main_plan_hop",
        _skip_plan,
    )
    monkeypatch.setattr(
        "workflows.implementation.code_pipeline.graph.decide_mutating_dispatch_allowed",
        lambda **_kwargs: Pause(
            reason="dispatch_ledger_active_hop stage=APPLY_IMPLEMENT run_ids=run-stale",
        ),
    )
    state: GraphState = {
        "change_id": "visual",
        "repository_path": str(tmp_path),
        "implementation_stage": STAGE_APPLY_IMPLEMENT,
        "implementation_slice_id": "SLICE-004",
        "implementation_status": "RUNNING",
        "live_execution_authorized": True,
    }
    patch = await implementation_execute_hop_node(
        state,
        agents=code_lane_agents_from_hop_agents(_hop_agents()),
    )
    assert patch["implementation_status"] == RUN_STATUS_PAUSED
    assert "implementation_last_hop" not in patch
    assert patch["implementation_recovery_code"] == "DISPATCH_LEDGER_ACTIVE_HOP"


@pytest.mark.asyncio
async def test_stale_resume_pre_agent_pause_routes_execute_not_plan_hop():
    """PAUSE без SDK run не должен stale-resume в ``implementation_route``."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from langgraph.checkpoint.memory import MemorySaver

    from workflows.checkpoint_resume import _resolve_stale_end_on_snapshot, _routing_state_for_stale_implementation_resume
    from workflows.implementation.graph import ImplementationHopAgents, build_implementation_graph
    from workflows.implementation.routing_contract import STAGE_APPLY_IMPLEMENT

    class _StubAgent:
        async def run(self, state):  # type: ignore[no-untyped-def]
            _ = state
            return {}

    agents = ImplementationHopAgents(
        apply_design=_StubAgent(),  # type: ignore[arg-type]
        apply_design_review=_StubAgent(),  # type: ignore[arg-type]
        apply_scaffold=_StubAgent(),  # type: ignore[arg-type]
        apply_red=_StubAgent(),  # type: ignore[arg-type]
        apply_implement=_StubAgent(),  # type: ignore[arg-type]
        apply_green=_StubAgent(),  # type: ignore[arg-type]
        implementation_arbiter=_StubAgent(),  # type: ignore[arg-type]
        scenario_coverage_plan=_StubAgent(),  # type: ignore[arg-type]
        scenario_test_author=_StubAgent(),  # type: ignore[arg-type]
    )
    graph = build_implementation_graph(agents, checkpointer=MemorySaver())
    snapshot = SimpleNamespace(
        next=(),
        values={
            "change_id": "visual",
            "implementation_status": RUN_STATUS_PAUSED,
            "implementation_recovery_code": "STAGE_NOT_WIRED",
            "implementation_stage": STAGE_APPLY_IMPLEMENT,
            "implementation_slice_id": "SLICE-004",
            "implementation_last_hop": STAGE_APPLY_IMPLEMENT,
            "implementation_plan": {
                "status": "RUNNING",
                "next_stage": STAGE_APPLY_IMPLEMENT,
                "next_slice": "SLICE-004",
                "live_execution_authorized": True,
                "plan_only": True,
            },
        },
    )
    routing_state = _routing_state_for_stale_implementation_resume(snapshot.values)
    config: dict[str, object] = {"configurable": {"thread_id": "verify-impl:visual"}}
    with (
        patch(
            "workflows.checkpoint_resume._infer_checkpoint_finished_node",
            AsyncMock(return_value="apply_code_lane"),
        ),
        patch(
            "workflows.checkpoint_resume.current_graph_successors_after_node",
            AsyncMock(return_value=["implementation_route"]),
        ),
    ):
        successor = await _resolve_stale_end_on_snapshot(
            graph,
            config,
            snapshot,
            routing_state,
        )
    assert successor in (
        "apply_code_lane",
        "implementation_execute_hop",
        "implementation_one_slice",
    )
    assert successor != _LEGACY_PLAN_ROUTE_NODE


def test_stale_skip_plan_hop_when_lane_running_forward_stage():
    """Stale END после plan: RUNNING + APPLY_IMPLEMENT → execute, не повторный plan."""
    from workflows.checkpoint_resume import _stale_successor_skip_plan_hop_to_execute
    class _Graph:
        nodes = {"implementation_execute_hop": object()}

    successor = _stale_successor_skip_plan_hop_to_execute(
        _Graph(),
        finished_node="implementation_route",
        snapshot_values={
            "implementation_status": RUN_STATUS_RUNNING,
            "implementation_stage": STAGE_APPLY_IMPLEMENT,
            "implementation_slice_id": "SLICE-004",
        },
        planned_successor=_LEGACY_PLAN_ROUTE_NODE,
    )
    assert successor == "implementation_execute_hop"


def test_stale_prefer_execute_over_plan_route_when_plan_forward():
    """Подграф stale END: plan RUNNING + PAUSE → execute, не повторный plan hop."""
    from workflows.checkpoint_resume import (
        _routing_state_for_stale_implementation_resume,
        _stale_implementation_prefer_execute_over_plan_route,
    )
    from workflows.implementation.routing_contract import (
        RUN_STATUS_PAUSED,
        STAGE_APPLY_IMPLEMENT,
    )

    lane = {
        "implementation_status": RUN_STATUS_PAUSED,
        "implementation_recovery_code": "DISPATCH_LEDGER_ACTIVE_HOP",
        "implementation_stage": STAGE_APPLY_IMPLEMENT,
        "implementation_plan": {
            "status": "RUNNING",
            "next_stage": STAGE_APPLY_IMPLEMENT,
            "next_slice": "SLICE-004",
            "plan_only": True,
        },
    }
    routing = _routing_state_for_stale_implementation_resume(lane)
    successor = _stale_implementation_prefer_execute_over_plan_route(
        lane,
        routing,
        _LEGACY_PLAN_ROUTE_NODE,
    )
    assert successor == "implementation_execute_hop"
