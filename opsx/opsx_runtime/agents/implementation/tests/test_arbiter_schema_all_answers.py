"""Валидатор ``arbiter_request`` во всех split-apply answer schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.implementation.apply_design.schemas import ApplyDesignAnswer
from agents.implementation.apply_design_review.schemas import ApplyDesignReviewAnswer
from agents.implementation.apply_green.schemas import ApplyGreenAnswer
from agents.implementation.apply_implement.schemas import ApplyImplementAnswer
from agents.implementation.apply_red.schemas import ApplyRedAnswer
from agents.implementation.apply_scaffold.schemas import ApplyScaffoldAnswer
from agents.implementation.arbiter_contract import ArbiterRequest
from agents.implementation.implementation_arbiter.schemas import ArbiterDecision


def _req() -> ArbiterRequest:
    return ArbiterRequest(
        reason="design gap",
        blocker_code="ORACLE_UNPROVEN",
        evidence=["missing binding"],
        suggested_target_stage="APPLY_DESIGN",
    )


def _split_base() -> dict[str, object]:
    return {
        "tasks_summary": "t",
        "gap_closure_summary": "g",
        "files_changed": [],
        "remaining_gaps": [],
    }


@pytest.mark.parametrize(
    "model",
    [
        ApplyScaffoldAnswer,
        ApplyRedAnswer,
        ApplyImplementAnswer,
        ApplyGreenAnswer,
        ApplyDesignAnswer,
    ],
)
def test_split_apply_escalate_requires_request(model: type) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({**_split_base(), "status": "ESCALATE_ARBITER"})


@pytest.mark.parametrize(
    "model",
    [
        ApplyScaffoldAnswer,
        ApplyRedAnswer,
        ApplyImplementAnswer,
        ApplyGreenAnswer,
        ApplyDesignAnswer,
    ],
)
def test_split_apply_complete_forbids_request(model: type) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(
            {
                **_split_base(),
                "status": "APPLY_COMPLETE",
                "arbiter_request": _req().model_dump(mode="json"),
            },
        )


def test_design_review_escalate_requires_request() -> None:
    with pytest.raises(ValidationError):
        ApplyDesignReviewAnswer.model_validate(
            {
                "status": "ESCALATE_ARBITER",
                "review_summary": "r",
                "findings_summary": "f",
            },
        )


def test_arbiter_decision_close_without_target_ok() -> None:
    dec = ArbiterDecision.model_validate(
        {
            "target_stage": None,
            "directive": "stop",
            "rationale": "owner",
            "close_without_target": True,
        },
    )
    assert dec.target_stage is None


def test_arbiter_decision_requires_target_when_not_close() -> None:
    with pytest.raises(ValidationError):
        ArbiterDecision.model_validate(
            {
                "directive": "go",
                "rationale": "x",
                "close_without_target": False,
            },
        )
