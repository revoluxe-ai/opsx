from __future__ import annotations

import pytest

from workflows.repair.control import (
    can_fanin,
    classification_route,
    claim_next,
    materialize_review,
    merge_same_invariant_candidates,
    stable_finding_id,
)
from workflows.repair.schemas import (
    ClassificationPayload,
    Finding,
    FindingOutcome,
    HuntCandidate,
    RelationPayload,
    RepairState,
)


def base_state(tmp_path, **updates):
    data = {
        "mission_id": "m1",
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "isolated_workspace": True,
        "output_dir": str(tmp_path / "run"),
    }
    data.update(updates)
    return RepairState(**data)


def test_stable_finding_id_is_deterministic():
    assert stable_finding_id("x", "a.py:1") == stable_finding_id("x", "a.py:1")


def test_materialize_clean_is_empty():
    assert materialize_review({"status": "CLEAN", "findings": []}) == []


def test_materialize_findings_builds_queue():
    queue = materialize_review(
        {
            "status": "FINDINGS",
            "findings": [
                {
                    "severity": "high",
                    "location": "a.py:10",
                    "finding": "broken edge",
                }
            ],
        }
    )
    assert len(queue) == 1
    assert queue[0].severity == "high"
    assert queue[0].root_relation == "ROOT"


def test_materialize_deduplicates_same_explicit_id():
    queue = materialize_review(
        {
            "status": "FINDINGS",
            "findings": [
                {"id": "F1", "severity": "low", "location": "a:1", "finding": "x"},
                {"id": "F1", "severity": "low", "location": "a:1", "finding": "x"},
            ],
        }
    )
    assert [x.id for x in queue] == ["F1"]


def test_materialize_blocked_raises():
    with pytest.raises(ValueError):
        materialize_review({"status": "BLOCKED", "block_reason": "no diff"})


def test_claim_next_skips_seen(tmp_path):
    f1 = Finding(id="F1", finding="x", location="a:1")
    f2 = Finding(id="F2", finding="y", location="b:2")
    state = base_state(tmp_path, queue=[f1, f2], seen=["F1"])
    claimed, remaining = claim_next(state)
    assert claimed == f2
    assert remaining == [f1]


@pytest.mark.parametrize(
    ("kind", "route"),
    [
        ("IMPLEMENTATION_BUG", "repair"),
        ("NEW_REQUIREMENT", "opsx_new"),
        ("ARCHIVED_PLANNING_BUG", "archived_delta"),
        ("SPEC_BUG", "opsx_update"),
        ("SPEC_GAP", "opsx_update"),
        ("FALSE_POSITIVE", "reject"),
        ("OUT_OF_SCOPE", "out_of_scope"),
        ("INCONCLUSIVE", "blocked"),
    ],
)
def test_classification_routes(kind, route):
    chosen = {
        "IMPLEMENTATION_BUG": "ROUTE_A",
        "NEW_REQUIREMENT": "ROUTE_NEW_CHANGE",
        "ARCHIVED_PLANNING_BUG": "ROUTE_ARCHIVED_DELTA",
        "SPEC_BUG": "ROUTE_B",
        "SPEC_GAP": "ROUTE_B",
        "FALSE_POSITIVE": "ROUTE_C",
        "OUT_OF_SCOPE": "ROUTE_D",
        "INCONCLUSIVE": "BLOCKED",
    }[kind]
    status = "BLOCKED" if kind == "INCONCLUSIVE" else "PASS"
    item = ClassificationPayload(
        status=status,
        kind=kind,
        reason="r",
        evidence="e",
        chosen_route=chosen,
    )
    assert classification_route(item) == route


def test_same_invariant_candidate_is_queued(tmp_path):
    candidate = HuntCandidate(
        hunt_candidate_id="HC-1",
        evidence_paths=["a.py"],
        observed_behavior="bad",
        possible_invariant_violation="same invariant broken",
        affected_component="a.py",
        confidence=0.9,
        location="a.py:3",
    )
    relation = RelationPayload(
        hunt_candidate_id="HC-1",
        relation="SAME_INVARIANT_FINDING",
        reason="same boundary",
        blocks_family_closure=True,
    )
    state = base_state(tmp_path)
    queue = merge_same_invariant_candidates(state, [(candidate, relation)])
    assert len(queue) == 1
    assert queue[0].id == "HC-1"


def test_false_positive_candidate_is_not_queued(tmp_path):
    candidate = HuntCandidate(
        hunt_candidate_id="HC-1",
        evidence_paths=[],
        observed_behavior="looks odd",
        possible_invariant_violation="maybe",
        affected_component="a.py",
        confidence=0.2,
    )
    relation = RelationPayload(
        hunt_candidate_id="HC-1",
        relation="FALSE_POSITIVE",
        reason="expected",
        blocks_family_closure=False,
    )
    state = base_state(tmp_path)
    assert merge_same_invariant_candidates(state, [(candidate, relation)]) == []


def test_fanin_blocked_by_unseen_same_invariant(tmp_path):
    relation = RelationPayload(
        hunt_candidate_id="HC-1",
        relation="SAME_INVARIANT_FINDING",
        reason="same",
        blocks_family_closure=True,
    )
    state = base_state(tmp_path, relations=[relation])
    ready, blockers = can_fanin(state)
    assert ready is False
    assert blockers == ["HC-1"]


def test_fanin_unblocks_after_same_invariant_is_terminal(tmp_path):
    finding = Finding(id="HC-1", finding="x", location="a:1")
    outcome = FindingOutcome(finding=finding, terminal_state="FIXED")
    relation = RelationPayload(
        hunt_candidate_id="HC-1",
        relation="SAME_INVARIANT_FINDING",
        reason="same",
        blocks_family_closure=True,
    )
    state = base_state(
        tmp_path,
        relations=[relation],
        seen=["HC-1"],
        finished=[outcome],
    )
    ready, blockers = can_fanin(state)
    assert ready is True
    assert blockers == []
