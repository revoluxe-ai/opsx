"""Conditional edges для изолированного design+review subgraph."""

from __future__ import annotations

from langgraph.graph import END

from collections.abc import Mapping
from typing import Any

from agents.state import GraphState
from workflows.implementation.routing_contract import (
    RUN_STATUS_APPLY_COMPLETE,
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_DESIGN,
    STAGE_APPLY_DESIGN_REVIEW,
    parse_super_implement_plan_payload,
)

DESIGN_LANE_ROUTE_NODE = "apply_design_lane_route"
DESIGN_LANE_EXECUTE_NODE = "apply_design_lane_execute"

DESIGN_LANE_STAGES = frozenset(
    {
        STAGE_APPLY_DESIGN,
        STAGE_APPLY_DESIGN_REVIEW,
    },
)


def design_lane_forward_plan_in_state(state: Mapping[str, Any]) -> bool:
    """True когда subgraph уже отдал parent forward ``implementation_plan`` (не design stage)."""
    status = str(state.get("implementation_status") or "")
    stage = str(state.get("implementation_stage") or "").strip()
    if not state.get("apply_design_lane_complete"):
        if not (status == "IN_PROGRESS" and not stage):
            return False
    raw = state.get("implementation_plan")
    if not isinstance(raw, dict):
        return False
    step = parse_super_implement_plan_payload(raw)
    next_stage = str(step.next_stage or "").strip()
    if step.status != RUN_STATUS_RUNNING or not next_stage:
        return False
    return next_stage not in DESIGN_LANE_STAGES


def route_after_design_lane_route(state: GraphState) -> str:
    """После plan: execute design/review, END при завершении lane или PAUSED/FAILED."""
    if design_lane_forward_plan_in_state(state):
        return END
    from workflows.implementation.routing import _raise_lane_terminal

    status = str(state.get("implementation_status") or "")
    if status in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED):
        _raise_lane_terminal(state)
    if state.get("apply_design_lane_complete"):
        return END
    if status == RUN_STATUS_APPLY_COMPLETE:
        return END
    if status == RUN_STATUS_RUNNING:
        stage = str(state.get("implementation_stage") or "")
        if stage in DESIGN_LANE_STAGES:
            return DESIGN_LANE_EXECUTE_NODE
    _raise_lane_terminal(state)


def route_after_design_lane_execute(state: GraphState) -> str:
    """После design — сразу review; после review — design или выход к слайсам."""
    if state.get("apply_design_lane_complete"):
        return END
    if state.get("implementation_arbiter_pending"):
        return END
    from workflows.implementation.routing import _raise_lane_terminal

    status = str(state.get("implementation_status") or "")
    if status in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED):
        _raise_lane_terminal(state)
    last_hop = str(state.get("implementation_last_hop") or "")
    stage = str(state.get("implementation_stage") or "")
    apply_design_status = str(state.get("apply_design_status") or "")
    if status == RUN_STATUS_RUNNING and last_hop == STAGE_APPLY_DESIGN:
        if stage == STAGE_APPLY_DESIGN_REVIEW:
            return DESIGN_LANE_EXECUTE_NODE
        if apply_design_status == "NO_CHANGE" and stage == STAGE_APPLY_DESIGN:
            return DESIGN_LANE_EXECUTE_NODE
    if status == RUN_STATUS_RUNNING and last_hop == STAGE_APPLY_DESIGN_REVIEW:
        if stage == STAGE_APPLY_DESIGN:
            return DESIGN_LANE_EXECUTE_NODE
    if status == RUN_STATUS_RUNNING:
        return DESIGN_LANE_ROUTE_NODE
    _raise_lane_terminal(state)
