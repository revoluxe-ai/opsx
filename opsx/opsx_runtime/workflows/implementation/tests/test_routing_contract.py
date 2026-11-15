"""Тесты JSON-контракта super-implement plan."""

from __future__ import annotations

import pytest

from workflows.implementation.plan_hop import build_main_lane_plan_patch
from workflows.implementation.routing import (
    ImplementationLaneTerminalError,
    OUTCOME_CONTINUE_EXECUTE,
    OUTCOME_RETURN_TO_DESIGN,
    route_after_apply_code_lane,
    route_after_execute_hop,
    route_after_plan_only,
)
from workflows.implementation.routing_contract import (
    RUN_STATUS_APPLY_COMPLETE,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_COMPLETE,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
    SuperImplementPlanStep,
    implementation_lane_should_execute,
    parse_super_implement_plan_payload,
)


def test_mid_slice_plan_design_does_not_force_design_hop() -> None:
    step = SuperImplementPlanStep(
        status=RUN_STATUS_RUNNING,
        live_execution_authorized=False,
        next_stage="APPLY_DESIGN",
        next_slice=None,
        primitive_command="/opsx-apply-design",
        code="DESIGN_FUTURE_SLICE_REPAIR",
        raw={"status": RUN_STATUS_RUNNING, "next_stage": "APPLY_DESIGN"},
    )
    patch = build_main_lane_plan_patch(
        step,
        prev_stage="APPLY_RED",
        prev_slice="SLICE-001",
        plan_reused=False,
    )
    assert patch.get("implementation_force_design_hop") is not True
    assert patch["implementation_status"] == RUN_STATUS_PAUSED
    assert patch["implementation_recovery_code"] == "DESIGN_REDO_MID_SLICE_FORBIDDEN"


def test_code_plan_design_stage_returns_to_design() -> None:
    """Plan APPLY_DESIGN из code lane — повтор design, не DESIGN_LANE_INCOMPLETE."""
    step = SuperImplementPlanStep(
        status=RUN_STATUS_RUNNING,
        live_execution_authorized=False,
        next_stage="APPLY_DESIGN",
        next_slice=None,
        primitive_command="/opsx-apply-design",
        code="DESIGN_FUTURE_SLICE_REPAIR",
        recovery_contract={
            "code": "DESIGN_FUTURE_SLICE_REPAIR",
            "next_action": "slice contract disagrees with the repository",
        },
        raw={"status": RUN_STATUS_RUNNING, "next_stage": "APPLY_DESIGN"},
    )
    patch = build_main_lane_plan_patch(
        step,
        prev_stage="APPLY_GREEN",
        prev_slice="SLICE-001",
        plan_reused=False,
    )
    assert patch["implementation_force_design_hop"] is True
    assert patch["implementation_status"] == RUN_STATUS_RUNNING
    assert patch["implementation_recovery_code"] == "DESIGN_FUTURE_SLICE_REPAIR"
    assert patch["implementation_machine_gate_errors"][0]["message"] == (
        "slice contract disagrees with the repository"
    )
    routed = {**patch, "implementation_plan_applied_without_hop": True}
    assert route_after_execute_hop(routed) == OUTCOME_RETURN_TO_DESIGN
    assert route_after_apply_code_lane(routed) == OUTCOME_RETURN_TO_DESIGN


def test_parse_plan_running_hop() -> None:
    step = parse_super_implement_plan_payload(
        {
            "status": RUN_STATUS_RUNNING,
            "plan_only": True,
            "next_stage": STAGE_APPLY_SCAFFOLD,
            "next_slice": "SLICE-002",
            "primitive_command": "/opsx-apply-scaffold",
            "live_execution_authorized": False,
        },
    )
    assert step.status == RUN_STATUS_RUNNING
    assert step.next_stage == STAGE_APPLY_SCAFFOLD
    assert step.next_slice == "SLICE-002"
    assert step.live_execution_authorized is False


def test_parse_plan_apply_complete() -> None:
    step = parse_super_implement_plan_payload(
        {
            "status": RUN_STATUS_APPLY_COMPLETE,
            "next_stage": STAGE_APPLY_COMPLETE,
            "live_execution_authorized": True,
            "auto_verify_forbidden": True,
        },
    )
    assert step.status == RUN_STATUS_APPLY_COMPLETE
    assert step.next_stage == STAGE_APPLY_COMPLETE


def test_implementation_lane_should_execute_in_progress_forward_stage() -> None:
    """``IN_PROGRESS`` + forward stage: plan hop не должен падать на END (double-plan симптом)."""
    state = {
        "implementation_status": "IN_PROGRESS",
        "implementation_stage": STAGE_APPLY_IMPLEMENT,
        "implementation_slice_id": "SLICE-004",
    }
    assert implementation_lane_should_execute(state) is True
    assert route_after_plan_only(state) == OUTCOME_CONTINUE_EXECUTE


def test_implementation_lane_should_execute_plan_supersedes_lane_pause() -> None:
    """Forward plan JSON RUNNING перекрывает runtime PAUSE — edge в execute, не END."""
    state = {
        "implementation_status": RUN_STATUS_PAUSED,
        "implementation_stage": STAGE_APPLY_IMPLEMENT,
        "implementation_plan": {
            "status": RUN_STATUS_RUNNING,
            "next_stage": STAGE_APPLY_IMPLEMENT,
            "next_slice": "SLICE-004",
            "live_execution_authorized": True,
            "plan_only": True,
        },
    }
    assert implementation_lane_should_execute(state) is True
    assert route_after_plan_only(state) == OUTCOME_CONTINUE_EXECUTE


def test_forward_apply_implement_without_execute_routes_to_end_regression_guard() -> None:
    """Нет executable stage и нет plan — остаёмся на END (не ложный execute)."""
    state = {
        "implementation_status": "IN_PROGRESS",
        "implementation_stage": None,
    }
    assert implementation_lane_should_execute(state) is False
    with pytest.raises(ImplementationLaneTerminalError):
        route_after_plan_only(state)


def test_implementation_lane_should_execute_from_plan_without_typed_stage() -> None:
    """RUNNING + forward plan без ``implementation_stage``: execute, не END (нет второго plan)."""
    forward_plan = {
        "status": RUN_STATUS_RUNNING,
        "plan_only": True,
        "next_stage": STAGE_APPLY_IMPLEMENT,
        "next_slice": "SLICE-004",
        "primitive_command": "/opsx-apply-implement",
        "live_execution_authorized": True,
    }
    state = {
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_plan": forward_plan,
    }
    assert implementation_lane_should_execute(state) is True
    assert route_after_plan_only(state) == OUTCOME_CONTINUE_EXECUTE
    route_after_plan_only(state)


def test_post_no_change_stagnation_forward_routes_execute() -> None:
    """После NO_CHANGE stagnation patch RUNNING + APPLY_RED → continue execute."""
    state = {
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_stage": STAGE_APPLY_RED,
        "implementation_slice_id": "SLICE-001",
        "implementation_plan": {
            "status": RUN_STATUS_RUNNING,
            "next_stage": STAGE_APPLY_RED,
            "next_slice": "SLICE-001",
            "plan_only": True,
            "live_execution_authorized": True,
        },
        "implementation_plan_applied_without_hop": False,
    }
    assert implementation_lane_should_execute(state) is True
    assert route_after_execute_hop(state) == OUTCOME_CONTINUE_EXECUTE


def test_implementation_lane_in_progress_forward_plan_routes_execute() -> None:
    """IN_PROGRESS после begin + plan JSON: conditional edge идёт в execute hop."""
    state = {
        "implementation_status": "IN_PROGRESS",
        "implementation_plan": {
            "status": RUN_STATUS_RUNNING,
            "next_stage": STAGE_APPLY_IMPLEMENT,
            "next_slice": "SLICE-004",
            "live_execution_authorized": True,
            "plan_only": True,
        },
    }
    assert route_after_plan_only(state) == OUTCOME_CONTINUE_EXECUTE


def test_parse_plan_recovery_paused_uses_recovery_code() -> None:
    """PAUSED plan: ``recovery_code`` попадает в ``step.code``."""
    step = parse_super_implement_plan_payload(
        {
            "status": RUN_STATUS_PAUSED,
            "recovery_code": "IMPLEMENT_RED_LINEAGE_BLOCKED",
            "next_stage": "RECOVERY_PAUSED",
            "next_slice": "SLICE-002",
            "recovery_contract": {"code": "IMPLEMENT_RED_LINEAGE_BLOCKED"},
            "live_execution_authorized": False,
        },
    )
    assert step.status == RUN_STATUS_PAUSED
    assert step.code == "IMPLEMENT_RED_LINEAGE_BLOCKED"
    assert step.recovery_contract is not None
