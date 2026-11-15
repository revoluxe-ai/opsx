"""Hermetic tests for hunt → orch explicit R1 handoff (Temporal kernel)."""

from __future__ import annotations

import json
from pathlib import Path

from cursor_adapter import MockCursorAdapter
from hunt_orch_handoff import (
    build_handoff_payload,
    build_repair_prompt,
    mint_hunt_repair_mission_id,
)
from lanes import make_project_bug_hunt_lane
from loop_engine import ReviewRepairLoop

FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "project_bug_hunt"
    / "hunt_orch_handoff_pbh008_009_incident.json"
)


def test_incident_fixture_mints_current_batch_mission_not_prior_terminal() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    finding_ids = data["current_hunt_repairable_findings"]
    ids = [row["finding_id"] for row in finding_ids]
    mission = mint_hunt_repair_mission_id(data["incident"]["change"], ids)
    assert mission == data["expected_mission_id"]
    assert mission != data["forbidden_mission_id"]


def test_repair_prompt_admits_r1_findings_and_binding() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    review = {
        "repairable_findings": data["current_hunt_repairable_findings"],
        "fingerprint": "fp-pbh-008-009",
    }
    handoff = build_handoff_payload(
        review=review,
        change=data["incident"]["change"],
        orchestration_execution_id=data["incident"]["temporal_run_id"],
        workflow_run_id=data["incident"]["temporal_run_id"],
        repair_number=1,
        review_fingerprint="fp-pbh-008-009",
        head_sha=data["incident"]["candidate_head"],
    )
    lane = make_project_bug_hunt_lane(change=data["incident"]["change"])
    prompt = build_repair_prompt(lane, handoff)
    assert "SOURCE_PRECEDENCE: R1_INLINE" in prompt
    assert handoff["mission_id"] in prompt
    assert "PBH-008" in prompt
    assert "PBH-009" in prompt
    assert data["incident"]["temporal_run_id"] in prompt
    assert data["incident"]["candidate_head"] in prompt
    assert data["forbidden_mission_id"] not in prompt


def test_loop_uses_handoff_prompt_for_project_bug_hunt() -> None:
    from cursor_adapter import MockScenario, MockScenarioStep, build_hunt_orch_clear_output

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    handoff = build_handoff_payload(
        review={"repairable_findings": data["current_hunt_repairable_findings"]},
        change=data["incident"]["change"],
        orchestration_execution_id=data["incident"]["temporal_run_id"],
        workflow_run_id=data["incident"]["temporal_run_id"],
        repair_number=1,
        review_fingerprint="fp",
        head_sha=data["incident"]["candidate_head"],
    )
    lane = make_project_bug_hunt_lane(change=data["incident"]["change"])
    prompt = build_repair_prompt(lane, handoff)
    adapter = MockCursorAdapter(
        MockScenario(
            name="handoff-repair-only",
            steps=[MockScenarioStep(prompt, build_hunt_orch_clear_output())],
        )
    )
    loop = ReviewRepairLoop(adapter, lane=lane, hunt_repair_handoff=handoff)
    _ = loop.run_single_repair()
    assert "HUNT_FINDINGS:" in adapter.history[0].prompt
    assert handoff["mission_id"] in adapter.history[0].prompt


def test_terminal_mission_superseded_when_findings_digest_changes(tmp_path: Path) -> None:
    from bug_lifecycle.orch_mission_runtime import advance, store_state
    from bug_lifecycle.stage_authority import Stage

    root = tmp_path / "ws"
    mission = "ORCH-invest-yalta-pbh-006-007"
    state = {
        "stage": Stage.DONE.value,
        "work_items": [],
        "completed_stages": [],
        "identity": {
            "mission_id": mission,
            "execution_id": "old-exec",
            "head": "a" * 40,
            "change_id": "invest-yalta-max-funnel",
            "findings_digest": "deadbeef",
        },
    }
    store_state(root, mission, state)
    request = {
        "mission": mission,
        "execution_id": "01a17ebd-d364-76c8-b34e-64a744657045",
        "head": "9e20974da8b98d8e09288b1d6a3879d371504fa1",
        "change": "invest-yalta-max-funnel",
        "findings": [
            {"location": "src/a.py:1", "finding": "PBH-008"},
            {"location": "src/b.py:2", "finding": "PBH-009"},
        ],
        "native_mission": "YES",
    }
    result = advance(root, request)
    assert result.get("REASON") == "terminal_mission_superseded"
