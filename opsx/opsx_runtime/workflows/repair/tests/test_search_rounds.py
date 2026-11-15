"""Тесты post-complete rehunt control."""

from __future__ import annotations

from workflows.repair.control import (
    can_post_complete_rehunt,
    post_complete_rehunt_update,
)
from workflows.repair.schemas import (
    FanInPayload,
    Finding,
    FindingOutcome,
    FixEvidence,
    GreenEvidence,
    HuntCandidate,
    HuntPayload,
    InvariantPayload,
    RepairState,
)


def _closed_state(
    *,
    search_round: int = 0,
    max_search_rounds: int = 3,
    last_hunt: str = "CLEAR",
) -> RepairState:
    finding = Finding(id="F1", finding="bug", location="a:1")
    outcome = FindingOutcome(
        finding=finding,
        fix=FixEvidence(status="FIXED", summary="ok", files_changed=["a.py"]),
        green=GreenEvidence(
            status="GREEN_CONFIRMED",
            test_id="t::x",
            command="pytest t::x",
        ),
        terminal_state="FIXED",
    )
    return RepairState(
        mission_id="m1",
        repository_path=".",
        workspace=".",
        output_dir=".",
        status="COMPLETE_NO_CLASS_LESSON",
        finished=[outcome],
        invariants=[
            InvariantPayload(
                invariant_id="INV-1",
                plain_language_invariant="rule",
                technical_invariant="rule tech",
                scope="scope",
                proof_from_root_finding="proof",
            )
        ],
        fanin=FanInPayload(
            status="FANIN_READY",
            root_bug="r",
            root_cause_pattern="p",
            error_class="e",
            regression_test_pattern="t",
        ),
        search_round=search_round,
        max_search_rounds=max_search_rounds,
        hunts=[
            HuntPayload(hunt_result="CLEAR")
            if last_hunt == "CLEAR"
            else HuntPayload(
                hunt_result="CANDIDATES_FOUND",
                candidates=[
                    HuntCandidate(
                        hunt_candidate_id="HC-1",
                        observed_behavior="x",
                        possible_invariant_violation="y",
                        affected_component="z",
                        confidence=0.5,
                    )
                ],
            )
        ]
        if last_hunt
        else [],
    )


def test_can_rehunt_after_first_close_even_if_preclose_hunt_clear():
    assert can_post_complete_rehunt(
        _closed_state(search_round=0, last_hunt="CLEAR")
    )


def test_clear_verification_hunt_stops_further_rounds():
    assert not can_post_complete_rehunt(
        _closed_state(search_round=1, last_hunt="CLEAR", max_search_rounds=3)
    )


def test_can_rehunt_after_fix_when_last_hunt_had_candidates():
    assert can_post_complete_rehunt(
        _closed_state(search_round=2, last_hunt="CANDIDATES_FOUND", max_search_rounds=3)
    )


def test_max_search_rounds_is_hard_cap():
    assert not can_post_complete_rehunt(
        _closed_state(search_round=1, last_hunt="CANDIDATES_FOUND", max_search_rounds=1)
    )


def test_post_complete_rehunt_clears_fanin_and_increments_round():
    state = _closed_state(search_round=2)
    update = post_complete_rehunt_update(state)
    assert update["search_round"] == 3
    assert update["fanin"] is None
    assert update["current_stage"] == "hunt"
