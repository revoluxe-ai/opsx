"""Project-bug-hunt lane: hunt → orch cycle on the shared Temporal kernel."""

from config import PROJECT_BUG_HUNT_REPAIR_PROMPT, PROJECT_BUG_HUNT_REVIEW_PROMPT, REPAIR_PROMPT
from cursor_adapter import (
    MockCursorAdapter,
    build_hunt_clean_output,
    build_hunt_findings_output,
    build_scenario,
)
from lanes import PROJECT_BUG_HUNT_LANE, get_lane, make_project_bug_hunt_lane
from loop_engine import ReviewRepairLoop
from parsers import parse_opsx_bug_fix_result, parse_project_bug_hunt_result


def test_hunt_prompts_use_tokens_and_orch():
    lane = make_project_bug_hunt_lane(
        base="stage",
        head="save-game-progress",
        change="cross-device-novel-save-refresh",
    )
    assert lane.review_prompt.startswith(
        "/opsx-project-bug-hunt base=stage head=save-game-progress "
        "change=cross-device-novel-save-refresh"
    )
    assert "/review-bugbot" not in lane.review_prompt
    assert "/review-security" not in lane.review_prompt
    assert lane.repair_prompt.startswith(
        "/opsx-orch-bug-fix change=cross-device-novel-save-refresh"
    )
    assert lane.repair_prompt != REPAIR_PROMPT
    assert PROJECT_BUG_HUNT_REVIEW_PROMPT.format(
        base="stage",
        head="save-game-progress",
        change="cross-device-novel-save-refresh",
    ) == lane.review_prompt
    assert PROJECT_BUG_HUNT_REPAIR_PROMPT.format(
        change="cross-device-novel-save-refresh"
    ) == lane.repair_prompt


def test_parse_hunt_findings_and_clean():
    findings = parse_project_bug_hunt_result(build_hunt_findings_output())
    assert findings.completed is True
    assert findings.clean is False
    assert len(findings.repairable_findings) == 1
    assert findings.repairable_findings[0]["location"].startswith("src/api/")

    clean = parse_project_bug_hunt_result(build_hunt_clean_output())
    assert clean.completed is True
    assert clean.clean is True
    assert clean.repairable_findings == []


def test_parse_hunt_process_only_is_product_clean():
    raw = """STATUS: HUNT_COMPLETE
PROJECT_BUG_HUNT_RECEIPT:
- findings_total: 1

FINDINGS:
### FINDING_ID: PBH-900
- TITLE: Process tooling leak
- LOC_LINK: tools/temporal/start.py:10
- SEVERITY: High
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is True
    assert parsed.clean is True
    assert parsed.repairable_findings == []
    assert len(parsed.excluded_findings) == 1


def test_parse_orch_repaired_this_batch_no():
    raw = """STATUS: PARTIAL_COMPLETE
BUG_BATCH_CLEAR: NO
REPAIRED_THIS_BATCH: NO
"""
    parsed = parse_opsx_bug_fix_result(raw)
    assert parsed.completed is True
    assert parsed.bug_batch_clear is False
    assert parsed.repaired_this_batch is False


def test_parse_orch_defer_only_bug_batch_clear_is_no_repair():
    raw = """STATUS: BUG_BATCH_CLEAR
MISSION_STATUS: BUG_BATCH_CLEAR
BUG_BATCH_CLEAR: YES
OWNER_AGGREGATE:
  FIX_NOW: (none — no MUST_FIX in admitted batch)
  DEFERRED_PRODUCT_BUGS:
    - PBH-002: DEFER_PRODUCT_BUG
"""
    parsed = parse_opsx_bug_fix_result(raw)
    assert parsed.completed is True
    assert parsed.bug_batch_clear is True
    assert parsed.repaired_this_batch is False


def test_parse_orch_fix_now_item_infers_repaired_yes():
    raw = """STATUS: BUG_BATCH_CLEAR
BUG_BATCH_CLEAR: YES
OWNER_AGGREGATE:
  FIX_NOW:
    - PBH-001: FIXED
"""
    parsed = parse_opsx_bug_fix_result(raw)
    assert parsed.repaired_this_batch is True


def test_get_lane_project_bug_hunt():
    lane = get_lane("project-bug-hunt", change="my-change")
    assert lane.id == PROJECT_BUG_HUNT_LANE.id
    assert "change=my-change" in lane.review_prompt
    assert lane.git_preflight is False
    assert lane.clean_status == "HUNT_CLEAN"


def test_project_bug_hunt_scenario1_mock_loop():
    outcome = ReviewRepairLoop(
        MockCursorAdapter(build_scenario("project-bug-hunt-scenario1")),
        lane=PROJECT_BUG_HUNT_LANE,
    ).run()
    assert outcome.status == "HUNT_CLEAN"
    assert outcome.repair_rounds == 2
    assert outcome.review_runs == 3


def test_project_bug_hunt_stops_when_orch_filters_without_repair():
    outcome = ReviewRepairLoop(
        MockCursorAdapter(build_scenario("project-bug-hunt-no-repair")),
        lane=PROJECT_BUG_HUNT_LANE,
    ).run()
    assert outcome.status == "NO_REPAIR_PROGRESS"
    assert outcome.repair_rounds == 1


def test_project_bug_hunt_stops_on_defer_only_bug_batch_clear():
    outcome = ReviewRepairLoop(
        MockCursorAdapter(build_scenario("project-bug-hunt-defer-only")),
        lane=PROJECT_BUG_HUNT_LANE,
    ).run()
    assert outcome.status == "NO_REPAIR_PROGRESS"
    assert outcome.repair_rounds == 1
    assert outcome.last_repair is not None
    assert outcome.last_repair.bug_batch_clear is True
    assert outcome.last_repair.repaired_this_batch is False
