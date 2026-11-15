"""Тесты mapping hop answer → implementation_status."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.implementation.apply_red.schemas import ApplyRedAnswer
from agents.implementation.hop_lane_status import (
    graph_dispatch_answer,
    implementation_lane_patch_for_hop_answer,
    merge_split_apply_hop_result,
)
from workflows.implementation.routing_contract import RUN_STATUS_PAUSED, RUN_STATUS_RUNNING


def test_escalate_arbiter_stays_running_pending() -> None:
    patch = implementation_lane_patch_for_hop_answer(
        "ESCALATE_ARBITER",
        result={
            "apply_red_answer": {
                "status": "ESCALATE_ARBITER",
                "arbiter_request": {
                    "reason": "design",
                    "blocker_code": "X",
                    "evidence": [],
                },
            },
        },
    )
    assert patch["implementation_status"] == RUN_STATUS_RUNNING
    assert patch["implementation_arbiter_pending"] is True
    assert patch["implementation_arbiter_request"]["blocker_code"] == "X"


def test_blocked_maps_to_paused() -> None:
    patch = implementation_lane_patch_for_hop_answer("BLOCKED")
    assert patch["implementation_status"] == RUN_STATUS_PAUSED
    assert patch["implementation_recovery_code"] == "APPLY_BLOCKED"
    assert "error" not in patch


def test_blocked_returns_machine_gate_error() -> None:
    """BLOCKED кладёт текст machine gate в ``error``, не код reject."""
    patch = implementation_lane_patch_for_hop_answer(
        "BLOCKED",
        result={
            "apply_scaffold_status": "BLOCKED",
            "implementation_machine_gate_errors": [
                {
                    "code": "SCHEMA",
                    "message": "SCAFFOLD.json is not a scaffold receipt",
                },
            ],
            "implementation_plan_reject_code": "SCAFFOLD_OPERATIVE_GATE_FAILED",
        },
    )
    assert patch["implementation_recovery_code"] == "APPLY_BLOCKED"
    assert patch["error"] == "SCHEMA: SCAFFOLD.json is not a scaffold receipt"


def test_apply_complete_stays_running_for_replan() -> None:
    patch = implementation_lane_patch_for_hop_answer("APPLY_COMPLETE")
    assert patch["implementation_status"] == RUN_STATUS_RUNNING


def test_dispatch_answer_arbiter_directive_stage_gated() -> None:
    gated = graph_dispatch_answer(
        {
            "implementation_stage": "APPLY_RED",
            "implementation_arbiter_target_stage": "APPLY_RED",
            "implementation_arbiter_directive": "do X",
        },
        "SLICE-001",
    )
    assert gated["arbiter_directive"] == "do X"
    muted = graph_dispatch_answer(
        {
            "implementation_stage": "APPLY_SCAFFOLD",
            "implementation_arbiter_target_stage": "APPLY_RED",
            "implementation_arbiter_directive": "do X",
        },
        "SLICE-001",
    )
    assert "arbiter_directive" not in muted


def test_dispatch_answer_drops_legacy_handoff() -> None:
    answer = graph_dispatch_answer(
        {
            "answer": {
                "slice_id": "SLICE-009",
                "next_command": "/opsx-apply-implement change=visual slice=SLICE-001",
                "local_hop_copy_block": "handoff",
                "next_action": "implement",
            },
        },
        "SLICE-001",
    )
    assert answer == {"slice_id": "SLICE-001"}


def test_dispatch_answer_carries_plan_reject() -> None:
    answer = graph_dispatch_answer(
        {
            "answer": {"slice_id": "SLICE-002"},
            "implementation_plan_reject_code": "RED_OPERATIVE_GATE_FAILED",
            "implementation_unchanged_dispatch_count": 1,
        },
        "SLICE-001",
    )
    assert answer["slice_id"] == "SLICE-001"
    assert answer["plan_rejected_code"] == "RED_OPERATIVE_GATE_FAILED"
    assert answer["unchanged_dispatch_attempt"] == 1


def test_apply_red_answer_rejects_next_command() -> None:
    with pytest.raises(ValidationError):
        ApplyRedAnswer.model_validate(
            {
                "status": "APPLY_COMPLETE",
                "tasks_summary": "done",
                "gap_closure_summary": "done",
                "next_command": "/opsx-apply-implement change=visual slice=SLICE-001",
            },
        )


def test_merge_preserves_agent_fields() -> None:
    merged = merge_split_apply_hop_result(
        {"apply_red_status": "BLOCKED", "run_id": "r1"},
        "BLOCKED",
    )
    assert merged["apply_red_status"] == "BLOCKED"
    assert merged["implementation_status"] == RUN_STATUS_PAUSED


def test_no_change_operative_valid_advances_lane() -> None:
    """visual scaffold: NO_CHANGE + plan authorized → RUNNING, не APPLY_BLOCKED."""
    hop_state = {
        "implementation_plan": {
            "prior_stage_operative": {
                "authorized": True,
                "artifact_status": "SCAFFOLD_READY",
            },
        },
    }
    patch = merge_split_apply_hop_result(
        {
            "apply_scaffold_status": "NO_CHANGE",
            "apply_scaffold_answer": {
                "status": "NO_CHANGE",
                "tasks_summary": "already on disk",
                "gap_closure_summary": "validator PASS",
                "files_changed": [],
                "remaining_gaps": [],
            },
        },
        "NO_CHANGE",
        hop_state=hop_state,
    )
    assert patch["implementation_status"] == RUN_STATUS_RUNNING
    assert "implementation_recovery_code" not in patch


def test_no_change_without_evidence_stays_paused() -> None:
    patch = implementation_lane_patch_for_hop_answer(
        "NO_CHANGE",
        result={
            "apply_scaffold_answer": {
                "status": "NO_CHANGE",
                "remaining_gaps": [],
            },
        },
        hop_state={
            "implementation_plan": {
                "prior_stage_operative": {"authorized": False, "code": "SCAFFOLD_MISSING"},
            },
        },
    )
    assert patch["implementation_status"] == RUN_STATUS_PAUSED
    assert patch["implementation_recovery_code"] == "APPLY_BLOCKED"


def test_no_change_apply_design_machine_pass_advances_lane() -> None:
    """NO_CHANGE + Python machine gate PASS → RUNNING (не зависит от prose агента)."""
    patch = merge_split_apply_hop_result(
        {
            "apply_design_status": "NO_CHANGE",
            "apply_design_answer": {
                "status": "NO_CHANGE",
                "tasks_summary": "validator PASS",
                "gap_closure_summary": "no edits",
                "files_changed": [],
                "remaining_gaps": [],
            },
            "implementation_machine_gate_errors": None,
        },
        "NO_CHANGE",
    )
    assert patch["implementation_status"] == RUN_STATUS_RUNNING
    assert "implementation_recovery_code" not in patch


def test_no_change_machine_gate_cleared_advances_lane() -> None:
    patch = merge_split_apply_hop_result(
        {
            "apply_red_status": "NO_CHANGE",
            "apply_red_answer": {
                "status": "NO_CHANGE",
                "remaining_gaps": [],
            },
            "implementation_machine_gate_errors": None,
        },
        "NO_CHANGE",
    )
    assert patch["implementation_status"] == RUN_STATUS_RUNNING
