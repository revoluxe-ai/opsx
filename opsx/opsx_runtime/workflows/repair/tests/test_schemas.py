from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from workflows.repair.schemas import (
    ClassificationPayload,
    FanInPayload,
    GreenEvidence,
    HuntPayload,
    ModelSelection,
    RedEvidence,
    RelationPayload,
    TokenUsage,
)
from agents.utils.response import parse_agent_json
from pydantic import BaseModel as _BaseModel


class _FenceProbe(_BaseModel):
    a: int


def test_default_model_selection_matches_super_low_profile():
    model = ModelSelection()
    assert model.id == "composer-2.5"
    param_map = {item.id: item.value for item in model.params}
    assert param_map["thinking"] == "true"
    assert param_map["reasoning_effort"] == "low"


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"input_tokens": 1, "cache_read_tokens": 2, "cache_write_tokens": 3}, 6),
        ({"input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}, 0),
        ({"input_tokens": None, "cache_read_tokens": 2, "cache_write_tokens": 3}, None),
        ({"input_tokens": 1, "cache_read_tokens": None, "cache_write_tokens": 3}, None),
        ({"input_tokens": 1, "cache_read_tokens": 2, "cache_write_tokens": None}, None),
    ],
)
def test_observed_input_tokens_unknown_is_not_zero(usage, expected):
    model = TokenUsage(**usage)
    assert model.observed_input_tokens() == expected


def test_classification_route_a_for_implementation_bug():
    item = ClassificationPayload(
        status="PASS",
        kind="IMPLEMENTATION_BUG",
        reason="implementation defect",
        evidence="file.py:1",
        chosen_route="ROUTE_A",
    )
    assert item.chosen_route == "ROUTE_A"


def test_classification_new_requirement_route():
    item = ClassificationPayload(
        status="PASS",
        kind="NEW_REQUIREMENT",
        reason="new capability",
        evidence="spec gap",
        chosen_route="ROUTE_NEW_CHANGE",
    )
    assert item.chosen_route == "ROUTE_NEW_CHANGE"


def test_classification_archived_planning_route():
    item = ClassificationPayload(
        status="PASS",
        kind="ARCHIVED_PLANNING_BUG",
        reason="archived spec wrong",
        evidence="openspec/decisions/x.md",
        chosen_route="ROUTE_ARCHIVED_DELTA",
    )
    assert item.chosen_route == "ROUTE_ARCHIVED_DELTA"


def test_classification_wrong_route_rejected():
    with pytest.raises(ValidationError):
        ClassificationPayload(
            status="PASS",
            kind="FALSE_POSITIVE",
            reason="no defect",
            evidence="trace",
            chosen_route="ROUTE_A",
        )


def test_red_confirmed_requires_failure_match_and_identity():
    with pytest.raises(ValidationError):
        RedEvidence(status="RED_CONFIRMED", failure_match=False)


def test_green_schema_accepts_non_weaken_delta():
    model = GreenEvidence(
        status="GREEN_CONFIRMED",
        test_id="t::x",
        command="pytest t::x",
        non_weaken_delta="fixture path only; assertion unchanged",
    )
    assert model.non_weaken_delta


def test_hunt_clear_rejects_candidates():
    with pytest.raises(ValidationError):
        HuntPayload(
            hunt_result="CLEAR",
            candidates=[
                {
                    "hunt_candidate_id": "HC-1",
                    "evidence_paths": [],
                    "observed_behavior": "x",
                    "possible_invariant_violation": "y",
                    "affected_component": "z",
                    "confidence": 0.8,
                }
            ],
        )


@pytest.mark.parametrize(
    ("relation", "blocks"),
    [
        ("SAME_INVARIANT_FINDING", True),
        ("INCONCLUSIVE", True),
        ("NEW_ROOT_CAUSE", False),
        ("FALSE_POSITIVE", False),
        ("OUT_OF_SCOPE", False),
    ],
)
def test_relation_closure_policy(relation, blocks):
    model = RelationPayload(
        hunt_candidate_id="HC-1",
        relation=relation,
        reason="evidence",
        blocks_family_closure=blocks,
    )
    assert model.blocks_family_closure is blocks


def test_relation_wrong_blocking_flag_rejected():
    with pytest.raises(ValidationError):
        RelationPayload(
            hunt_candidate_id="HC-1",
            relation="SAME_INVARIANT_FINDING",
            reason="evidence",
            blocks_family_closure=False,
        )


def test_fanin_ready_schema():
    model = FanInPayload(
        status="FANIN_READY",
        root_bug="root",
        root_cause_pattern="pattern",
        error_class="class",
        regression_test_pattern="same RED test",
    )
    assert model.status == "FANIN_READY"


def test_single_json_fence_is_accepted():
    raw = "```json\n{\"a\": 1}\n```"
    assert parse_agent_json(raw, _FenceProbe) == _FenceProbe(a=1)


def test_mixed_prose_is_not_salvaged():
    raw = "result: {\"a\": 1}"
    with pytest.raises(ValidationError):
        parse_agent_json(raw, _FenceProbe)
