"""ESCALATE_ARBITER → pending patch и route_after_arbiter."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.implementation.apply_red.schemas import ApplyRedAnswer
from agents.implementation.arbiter_contract import ArbiterRequest
from agents.implementation.hop_lane_status import (
    implementation_lane_patch_for_hop_answer,
    merge_split_apply_hop_result,
)
from workflows.implementation.arbiter_hop import OUTCOME_TARGET_DESIGN, route_after_arbiter
from workflows.implementation.routing import ImplementationLaneTerminalError
from workflows.implementation.routing_contract import (
    RUN_STATUS_RUNNING,
    STAGE_APPLY_DESIGN,
)


def test_escalate_requires_arbiter_request() -> None:
    with pytest.raises(ValidationError):
        ApplyRedAnswer.model_validate(
            {
                "status": "ESCALATE_ARBITER",
                "tasks_summary": "x",
                "gap_closure_summary": "y",
            },
        )


def test_escalate_maps_to_pending() -> None:
    req = ArbiterRequest(
        reason="design bindings missing",
        blocker_code="ORACLE_UNPROVEN",
        evidence=["no red_oracle_bindings"],
        suggested_target_stage=STAGE_APPLY_DESIGN,
    )
    answer = ApplyRedAnswer(
        status="ESCALATE_ARBITER",
        tasks_summary="cannot fix",
        gap_closure_summary="design gap",
        arbiter_request=req,
    )
    patch = merge_split_apply_hop_result(
        {"apply_red_status": answer.status, "apply_red_answer": answer.model_dump(mode="json")},
        answer.status,
    )
    assert patch["implementation_arbiter_pending"] is True
    assert patch["implementation_status"] == RUN_STATUS_RUNNING
    assert patch["implementation_arbiter_request"]["blocker_code"] == "ORACLE_UNPROVEN"


def test_route_after_arbiter_to_design() -> None:
    state = {
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_arbiter_target_stage": STAGE_APPLY_DESIGN,
    }
    assert route_after_arbiter(state) == OUTCOME_TARGET_DESIGN


def test_implementation_lane_patch_escalate_only() -> None:
    patch = implementation_lane_patch_for_hop_answer("ESCALATE_ARBITER")
    assert patch["implementation_arbiter_pending"] is True


def test_route_after_arbiter_slice_missing_context_raises() -> None:
    state = {
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_arbiter_target_stage": "APPLY_RED",
        "implementation_slices": None,
    }
    with pytest.raises(ImplementationLaneTerminalError, match="ARBITER_SLICE_CONTEXT_MISSING"):
        route_after_arbiter(state)
