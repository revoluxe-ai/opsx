"""Граф: эскалация → arbiter → design; budget; directive в dispatch."""

from __future__ import annotations

from typing import Any

import pytest

from agents.implementation.arbiter_contract import ArbiterRequest
from agents.implementation.hop_lane_status import graph_dispatch_answer
from agents.state import GraphState
from workflows.implementation.graph import ImplementationHopAgents, build_implementation_graph
from workflows.implementation.routing import (
    ARBITER_MAX_CALLS,
    ImplementationLaneTerminalError,
    OUTCOME_ENTER_ARBITER,
    route_after_one_slice,
)
from workflows.implementation.routing_contract import (
    RUN_STATUS_RUNNING,
    STAGE_APPLY_DESIGN,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)


class _EscalateRedStub:
    async def run(self, state: GraphState) -> dict[str, Any]:
        stage = str(state.get("implementation_stage") or "")
        if stage == STAGE_APPLY_RED:
            req = ArbiterRequest(
                reason="oracle",
                blocker_code="ORACLE_UNPROVEN",
                evidence=["no bindings"],
                suggested_target_stage=STAGE_APPLY_DESIGN,
            )
            return {
                "apply_red_status": "ESCALATE_ARBITER",
                "apply_red_answer": {
                    "status": "ESCALATE_ARBITER",
                    "tasks_summary": "t",
                    "gap_closure_summary": "g",
                    "arbiter_request": req.model_dump(mode="json"),
                },
                "implementation_status": RUN_STATUS_RUNNING,
                "implementation_arbiter_pending": True,
                "implementation_arbiter_request": req.model_dump(mode="json"),
            }
        return {}


class _ArbiterStub:
    last_model: dict[str, Any] | None = None

    async def run(self, state: GraphState) -> dict[str, Any]:
        _ArbiterStub.last_model = state.get("model")
        return {
            "implementation_arbiter_target_stage": STAGE_APPLY_DESIGN,
            "implementation_arbiter_directive": "add red_oracle_bindings for SLICE-009",
            "implementation_status": RUN_STATUS_RUNNING,
            "implementation_force_design_hop": True,
            "implementation_arbiter_directed_design_hop": True,
            "implementation_arbiter_pending": False,
        }


class _Stub:
    async def run(self, state: GraphState) -> dict[str, Any]:
        _ = state
        return {}


def _agents() -> ImplementationHopAgents:
    red = _EscalateRedStub()
    return ImplementationHopAgents(
        apply_design=_Stub(),  # type: ignore[arg-type]
        apply_design_review=_Stub(),  # type: ignore[arg-type]
        apply_scaffold=_Stub(),  # type: ignore[arg-type]
        apply_red=red,  # type: ignore[arg-type]
        apply_implement=_Stub(),  # type: ignore[arg-type]
        apply_green=_Stub(),  # type: ignore[arg-type]
        implementation_arbiter=_ArbiterStub(),  # type: ignore[arg-type]
        scenario_coverage_plan=_ArbiterStub(),  # type: ignore[arg-type]
        scenario_test_author=_ArbiterStub(),  # type: ignore[arg-type]
    )


def test_implementation_graph_compiles_with_arbiter_node() -> None:
    _ = build_implementation_graph(_agents(), checkpointer=None)


def test_pending_red_routes_to_arbiter() -> None:
    assert route_after_one_slice(
        {
            "implementation_arbiter_pending": True,
            "implementation_arbiter_calls": 0,
            "implementation_status": RUN_STATUS_RUNNING,
        },
    ) == OUTCOME_ENTER_ARBITER


@pytest.mark.asyncio
async def test_arbiter_node_uses_sonnet_model() -> None:
    from workflows.implementation.arbiter_hop import implementation_arbiter_node

    agent = _ArbiterStub()
    state: GraphState = {
        "implementation_arbiter_pending": False,
        "implementation_arbiter_calls": 0,
    }
    _ = await implementation_arbiter_node(state, agent=agent)  # type: ignore[arg-type]
    assert isinstance(_ArbiterStub.last_model, dict)
    assert _ArbiterStub.last_model.get("id") == "claude-sonnet-5-5"


def test_fourth_escalation_raises_budget() -> None:
    with pytest.raises(ImplementationLaneTerminalError, match="ARBITER_BUDGET_EXHAUSTED"):
        route_after_one_slice(
            {
                "implementation_arbiter_pending": True,
                "implementation_arbiter_calls": ARBITER_MAX_CALLS,
                "implementation_status": RUN_STATUS_RUNNING,
            },
        )


def test_directive_in_dispatch_only_for_matching_stage() -> None:
    state: GraphState = {
        "implementation_stage": STAGE_APPLY_RED,
        "implementation_arbiter_target_stage": STAGE_APPLY_RED,
        "implementation_arbiter_directive": "fix oracle",
    }
    ans = graph_dispatch_answer(state, "SLICE-009")
    assert ans.get("arbiter_directive") == "fix oracle"

    wrong_stage: GraphState = {
        **state,
        "implementation_stage": STAGE_APPLY_SCAFFOLD,
    }
    ans2 = graph_dispatch_answer(wrong_stage, "SLICE-009")
    assert "arbiter_directive" not in ans2
