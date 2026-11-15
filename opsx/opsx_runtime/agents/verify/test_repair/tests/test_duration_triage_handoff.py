"""Repair принимает синтетический triage после test_duration HANG_TEST."""

from __future__ import annotations

import pytest

from agents.verify.test_repair.triage_handoff import resolve_test_triage_for_repair


def test_promotes_hang_test_duration_to_test_triage() -> None:
    state = {
        "test_duration_result": {
            "classification": "HANG_TEST",
            "finding_id": "F-0001",
            "rationale": "deterministic duration",
        },
        "dispatcher_current_item": {"finding_id": "F-0001"},
    }
    triage = resolve_test_triage_for_repair(state)
    assert triage["classification"] == "TEST"
    assert triage["finding_id"] == "F-0001"
    assert "test_duration" in triage["rationale"]


def test_promotes_test_interaction_test_repair_owner() -> None:
    state = {
        "test_interaction_result": {
            "finding_id": "F-0001",
            "classification": "SHARED_RESOURCE",
            "repair_owner": "test_repair",
            "repro_request": "ordered_sequence",
            "rationale": "module-level cache",
        },
    }
    triage = resolve_test_triage_for_repair(state)
    assert triage["classification"] == "TEST"
    assert triage["finding_id"] == "F-0001"
    assert "test_interaction" in triage["rationale"]


def test_missing_both_raises() -> None:
    with pytest.raises(ValueError, match="test_triage_result missing"):
        resolve_test_triage_for_repair({})
