"""Stagnation guard: plan не сдвинулся после APPLY_COMPLETE."""

from __future__ import annotations

from unittest.mock import patch

from agents.state import GraphState
from workflows.implementation.hop_stagnation import stagnation_patch_after_hop
from workflows.implementation.routing_contract import (
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
    SuperImplementPlanStep,
)


def test_stagnation_retries_once_when_plan_repeats_dispatch(tmp_path) -> None:
    (tmp_path / "opsx").mkdir()
    state: GraphState = {
        "change_id": "visual",
        "repository_path": str(tmp_path),
    }
    step = SuperImplementPlanStep(
        status=RUN_STATUS_RUNNING,
        live_execution_authorized=False,
        next_stage=STAGE_APPLY_RED,
        next_slice="SLICE-001",
        raw={
            "prior_stage_operative": {"code": "RED_OPERATIVE_GATE_FAILED"},
        },
    )
    with patch(
        "workflows.implementation.hop_stagnation.stage_dispatch.plan_super_implement_step",
        return_value=step,
    ):
        patch_body = stagnation_patch_after_hop(
            state,
            executed_stage=STAGE_APPLY_RED,
            executed_slice="SLICE-001",
            hop_status="APPLY_COMPLETE",
        )
    assert patch_body["implementation_status"] == RUN_STATUS_RUNNING
    assert patch_body["implementation_recovery_code"] == "RED_OPERATIVE_GATE_FAILED"
    assert patch_body["implementation_plan_reject_code"] == "RED_OPERATIVE_GATE_FAILED"
    assert patch_body["implementation_unchanged_dispatch_count"] == 1


def test_stagnation_pauses_on_second_unchanged_dispatch(tmp_path) -> None:
    (tmp_path / "opsx").mkdir()
    state: GraphState = {
        "change_id": "visual",
        "repository_path": str(tmp_path),
        "implementation_unchanged_dispatch_count": 1,
    }
    step = SuperImplementPlanStep(
        status=RUN_STATUS_RUNNING,
        live_execution_authorized=False,
        next_stage=STAGE_APPLY_RED,
        next_slice="SLICE-001",
        raw={
            "prior_stage_operative": {"code": "RED_OPERATIVE_GATE_FAILED"},
        },
    )
    with patch(
        "workflows.implementation.hop_stagnation.stage_dispatch.plan_super_implement_step",
        return_value=step,
    ):
        patch_body = stagnation_patch_after_hop(
            state,
            executed_stage=STAGE_APPLY_RED,
            executed_slice="SLICE-001",
            hop_status="APPLY_COMPLETE",
        )
    assert patch_body["implementation_status"] == RUN_STATUS_PAUSED
    assert patch_body["implementation_unchanged_dispatch_count"] == 2


def test_no_change_scaffold_ready_refreshes_plan_to_red(tmp_path) -> None:
    """NO_CHANGE + SCAFFOLD_READY: после hop plan сдвигает stage (не loop scaffold)."""
    (tmp_path / "opsx").mkdir()
    state = {
        "change_id": "visual",
        "repository_path": str(tmp_path),
        "implementation_plan": {
            "prior_stage_operative": {
                "authorized": True,
                "artifact_status": "SCAFFOLD_READY",
            },
        },
    }
    step = SuperImplementPlanStep(
        status=RUN_STATUS_RUNNING,
        live_execution_authorized=True,
        next_stage=STAGE_APPLY_RED,
        next_slice="SLICE-001",
        primitive_command="/opsx-apply-red",
        raw={
            "status": RUN_STATUS_RUNNING,
            "next_stage": STAGE_APPLY_RED,
            "next_slice": "SLICE-001",
            "plan_only": True,
            "prior_stage_operative": {"authorized": True, "artifact_status": "SCAFFOLD_READY"},
        },
    )
    hop_result = {
        "apply_scaffold_status": "NO_CHANGE",
        "apply_scaffold_answer": {
            "status": "NO_CHANGE",
            "remaining_gaps": [],
            "tasks_summary": "ok",
            "gap_closure_summary": "pass",
            "files_changed": [],
        },
    }
    with patch(
        "workflows.implementation.hop_stagnation.stage_dispatch.plan_super_implement_step",
        return_value=step,
    ):
        patch_body = stagnation_patch_after_hop(
            state,
            executed_stage=STAGE_APPLY_SCAFFOLD,
            executed_slice="SLICE-001",
            hop_status="NO_CHANGE",
            hop_result=hop_result,
        )
    assert patch_body["implementation_plan_reuse_after_hop"] is True
    assert patch_body["implementation_stage"] == STAGE_APPLY_RED
    assert patch_body["implementation_slice_id"] == "SLICE-001"
    assert patch_body["implementation_unchanged_dispatch_count"] == 0


def test_no_change_without_operative_evidence_skips_plan_refresh(tmp_path) -> None:
    """NO_CHANGE без operative PASS — stagnation не трогает plan."""
    (tmp_path / "opsx").mkdir()
    state = {
        "change_id": "visual",
        "repository_path": str(tmp_path),
        "implementation_plan": {
            "prior_stage_operative": {"authorized": False, "code": "SCAFFOLD_NOT_READY"},
        },
    }
    with patch(
        "workflows.implementation.hop_stagnation.stage_dispatch.plan_super_implement_step",
    ) as plan_mock:
        patch_body = stagnation_patch_after_hop(
            state,
            executed_stage=STAGE_APPLY_SCAFFOLD,
            executed_slice="SLICE-001",
            hop_status="NO_CHANGE",
            hop_result={
                "apply_scaffold_answer": {"status": "NO_CHANGE", "remaining_gaps": []},
            },
        )
    plan_mock.assert_not_called()
    assert patch_body == {}
