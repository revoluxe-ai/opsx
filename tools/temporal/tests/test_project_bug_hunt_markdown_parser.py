"""Fail-closed разбор `/opsx-project-bug-hunt` и терминальные review-исходы.

Фикстура — verbatim `result_text` прогона consultation-funnel-baseline
(`review-retry-2`, prompt digest `0647eba50870e9df`), где агент оформил
machine block через `**STATUS: HUNT_COMPLETE**`, `findings_total: **2**` и
`**F-n [SEVERITY] Title**` + `LOC_LINK: \\`path:line\\``. Под pytest product
roots = `app/`, `tests/` (conftest), поэтому `src/stroy/...` попадает в excluded.
"""

import json
from pathlib import Path

import pytest
from activities import (
    ActivityConfig,
    configure_activities,
    ensure_cursor_session,
    execute_commit_materialize,
    execute_project_bug_hunt,
    execute_project_bug_hunt_orch,
)
from config import PROJECT_BUG_HUNT_REVIEW_PROMPT, TEMPORAL_TASK_QUEUE
from cursor_adapter import (
    MockCursorAdapter,
    MockScenario,
    MockScenarioStep,
    build_hunt_contract_invalid_output,
    _mock_hunt_repair_step,
    build_hunt_orch_clear_output,
    build_hunt_clean_output,
)
from lanes import PROJECT_BUG_HUNT_LANE, make_project_bug_hunt_lane
from loop_engine import ReviewRepairLoop
from models import (
    REVIEW_BLOCKED,
    REVIEW_CONTRACT_INVALID,
    REVIEW_FAILED,
    REVIEW_UNPARSEABLE,
    classify_incomplete_review,
    raw_output_sha256,
)
from owner_done_signal import classify_terminal_bucket, is_terminal_outcome
from parsers import (
    extract_hunt_findings,
    extract_hunt_status,
    parse_bugbot_result,
    parse_project_bug_hunt_result,
)
from tempo_operator import review_evidence_from_result
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from control_plane_code_identity import compute_control_plane_code_identity
from review_repair_terminal_policy import terminal_payload_from_application_error
from pbh_workflow_test_helpers import execute_pbh_with_admission
from workflow import ProjectBugHuntWorkflow

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "project_bug_hunt"
    / "markdown_hunt_complete_result.txt"
)
DUAL_REPR_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "project_bug_hunt"
    / "external_client_notifications_pilot_dual_repr_hunt_complete.txt"
)
INVEST_YALTA_DUPLICATE_TAIL_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "project_bug_hunt"
    / "invest_yalta_max_funnel_duplicate_findings_tail_hunt_complete.txt"
)
F1_LOC = "src/stroy/channels/telegram/invest_yalta/fsm.py:970"
F2_LOC = "tests/unit/channels/telegram/invest_yalta/test_p0_evidence_characterization.py:272-285"


def _fixture_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def _without_f2_block(raw: str) -> str:
    start = raw.index("**F-2 [LOW]")
    end = raw.index("\n---", start)
    return raw[:start] + raw[end:]


def _hunt_loop(*outputs: str, transient_failures: int = 0) -> tuple[ReviewRepairLoop, MockCursorAdapter]:
    steps = [
        MockScenarioStep(
            PROJECT_BUG_HUNT_LANE.review_prompt,
            output,
            transient_failures=transient_failures if index == 0 else 0,
        )
        for index, output in enumerate(outputs)
    ]
    adapter = MockCursorAdapter(MockScenario(name="hunt-parser-test", steps=steps))
    return ReviewRepairLoop(adapter, lane=PROJECT_BUG_HUNT_LANE, mock_mode=True), adapter


def test_markdown_hunt_result_completes_with_two_findings():
    raw = _fixture_text()
    parsed = parse_project_bug_hunt_result(raw)

    assert extract_hunt_status(raw) == "HUNT_COMPLETE"
    assert parsed.completed is True
    assert parsed.clean is False
    assert parsed.review_status == "HUNT_COMPLETE"
    assert parsed.parse_error_code is None
    assert parsed.parse_errors == []
    assert parsed.transient_error is False
    assert parsed.raw_output == raw
    assert len(parsed.findings) == 2
    assert [row["location"] for row in parsed.findings] == [F1_LOC, F2_LOC]
    assert [row["severity"] for row in parsed.findings] == ["Medium", "Low"]
    assert [row["finding_id"] for row in parsed.findings] == ["F-1", "F-2"]
    assert parsed.findings[0]["finding"].startswith(
        "F-1: Confirm-booking idempotency key is epoch-scoped"
    )
    assert [row["location"] for row in parsed.repairable_findings] == [F2_LOC]
    assert [row["location"] for row in parsed.excluded_findings] == [F1_LOC]


def test_declared_two_parsed_one_is_contract_invalid():
    parsed = parse_project_bug_hunt_result(_without_f2_block(_fixture_text()))
    assert parsed.completed is False
    assert parsed.clean is None
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID
    assert parsed.parse_errors == ["findings_total=2 but parsed 1 finding block(s)"]
    assert len(parsed.findings) == 1


def test_hunt_complete_nonzero_total_without_blocks_is_invalid():
    raw = """STATUS: HUNT_COMPLETE
PROJECT_BUG_HUNT_RECEIPT:
- findings_total: 2

FINDINGS:
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is False
    assert parsed.clean is None
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID
    assert parsed.findings == []


def test_findings_without_terminal_status_are_invalid():
    raw = _fixture_text().replace(
        "**STATUS: HUNT_COMPLETE** (product lane only; `tools/**`, `.cursor/**`, `opsx/**` excluded)\n",
        "",
    )
    assert "STATUS:" not in raw
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is False
    assert parsed.clean is None
    assert parsed.review_status is None
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID
    assert "missing terminal STATUS line" in parsed.parse_errors
    assert len(parsed.findings) == 2


def test_missing_loc_link_is_invalid():
    raw = _fixture_text().replace(f"LOC_LINK: `{F2_LOC}`\n", "")
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is False
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID
    assert parsed.parse_errors == ["F-2: missing LOC_LINK"]


def test_unusable_loc_link_and_duplicate_ids_are_invalid():
    raw = """STATUS: HUNT_COMPLETE
- findings_total: 2

### FINDING_ID: PBH-1
- TITLE: First
- LOC_LINK: app/api/a.js
### FINDING_ID: PBH-1
- TITLE: Second
- LOC_LINK: app/api/b.js:4
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID
    assert parsed.parse_errors == [
        "PBH-1: duplicate FINDING_ID block conflicts with earlier definition",
        "findings_total=2 but parsed 1 finding block(s)",
        "PBH-1: LOC_LINK 'app/api/a.js' is not repo-relative path:line",
    ]


def test_missing_or_conflicting_findings_total_is_invalid():
    missing = parse_project_bug_hunt_result("STATUS: HUNT_COMPLETE\nFINDINGS:\n")
    assert missing.parse_error_code == REVIEW_CONTRACT_INVALID
    assert missing.parse_errors == ["missing findings_total"]

    conflicting = parse_project_bug_hunt_result(
        "STATUS: HUNT_COMPLETE\n- findings_total: 0\n- findings_total: 1\n"
    )
    assert conflicting.parse_error_code == REVIEW_CONTRACT_INVALID
    assert conflicting.clean is None


def test_blocked_status_is_blocked_not_clean():
    raw = """**STATUS: BLOCKED_PREFLIGHT** (diff unavailable)
- findings_total: 0
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is False
    assert parsed.clean is None
    assert parsed.review_status == "BLOCKED_PREFLIGHT"
    assert parsed.parse_error_code is None
    assert parsed.transient_error is False
    assert classify_incomplete_review(parsed.to_dict())[0] == REVIEW_BLOCKED


def test_last_status_occurrence_still_wins():
    raw = """**STATUS: HUNT_COMPLETE**
- findings_total: 0
**STATUS: BLOCKED_PREFLIGHT** (diff unavailable)
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert extract_hunt_status(raw) == "BLOCKED_PREFLIGHT"
    assert parsed.review_status == "BLOCKED_PREFLIGHT"
    assert parsed.completed is False


def test_zero_findings_with_complete_receipt_is_clean():
    raw = """**STATUS: HUNT_COMPLETE**

**PROJECT_BUG_HUNT_RECEIPT**
- findings_total: **0**

FINDINGS:
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is True
    assert parsed.clean is True
    assert parsed.findings == []
    assert parsed.parse_error_code is None


def test_out_of_product_findings_keep_exclusion_evidence():
    raw = """**STATUS: HUNT_COMPLETE**
- findings_total: 1

**F-1 [HIGH] Process tooling leak**
LOC_LINK: `tools/temporal/start.py:10`
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is True
    assert parsed.clean is True
    assert parsed.repairable_findings == []
    assert parsed.excluded_findings == [
        {
            "severity": "High",
            "location": "tools/temporal/start.py:10",
            "finding": "F-1: Process tooling leak",
            "finding_id": "F-1",
        }
    ]


def test_bracket_finding_with_bullet_and_no_backticks():
    raw = """STATUS: HUNT_COMPLETE
- findings_total: 1

- **F-7 [HIGH] — Save request never aborts**
- LOC_LINK: app/api/softProdNovelSave.js:28
"""
    assert extract_hunt_findings(raw) == [
        {
            "severity": "High",
            "location": "app/api/softProdNovelSave.js:28",
            "finding": "F-7: Save request never aborts",
            "finding_id": "F-7",
        }
    ]
    assert parse_project_bug_hunt_result(raw).completed is True


def test_canonical_block_with_bold_and_code_spans():
    raw = """**STATUS:** `HUNT_COMPLETE`
- findings_total: `1`

### **FINDING_ID:** PBH-010
- TITLE: Missing abort
LOC_LINK: `app/api/softProdNovelSave.js:40`
- SEVERITY: Medium
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is True
    assert parsed.findings == [
        {
            "severity": "Medium",
            "location": "app/api/softProdNovelSave.js:40",
            "finding": "PBH-010: Missing abort",
            "finding_id": "PBH-010",
        }
    ]


def test_mixed_finding_formats_are_invalid():
    raw = """STATUS: HUNT_COMPLETE
- findings_total: 2

### FINDING_ID: PBH-1
- LOC_LINK: app/api/a.js:1

**F-2 [LOW] Other**
LOC_LINK: app/api/b.js:2
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID
    assert parsed.parse_errors == [
        "F-2: markdown finding has no matching canonical FINDING_ID block",
        "findings_total=2 but parsed 1 finding block(s)",
    ]


def test_duplicate_canonical_findings_tail_accepts_two_findings():
    """Regression: invest-yalta @ 66222d37 duplicated FINDINGS tail (4 → 2 blocks)."""
    raw = INVEST_YALTA_DUPLICATE_TAIL_FIXTURE.read_text(encoding="utf-8")
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is True
    assert parsed.parse_error_code is None
    assert len(parsed.findings) == 2
    assert [row["finding_id"] for row in parsed.findings] == ["PBH-001", "PBH-002"]


def test_dual_representation_same_ids_accept_four_findings():
    """Regression: external-client-notifications-pilot hunt duplicated PBH-001..004."""
    raw = DUAL_REPR_FIXTURE.read_text(encoding="utf-8")
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is True
    assert parsed.clean is False
    assert parsed.parse_error_code is None
    assert len(parsed.findings) == 4
    assert [row["finding_id"] for row in parsed.findings] == [
        "PBH-001",
        "PBH-002",
        "PBH-003",
        "PBH-004",
    ]
    assert parsed.raw_output == raw


def test_dual_representation_hunt_complete_is_not_hunt_clean():
    raw = DUAL_REPR_FIXTURE.read_text(encoding="utf-8")
    loop, adapter = _hunt_loop(raw)
    review = loop.run_single_review()
    assert review.completed is True
    assert review.clean is False
    assert len(adapter.history) == 1


def test_dual_representation_proceeds_to_repair_activity():
    raw = DUAL_REPR_FIXTURE.read_text(encoding="utf-8")
    hunt = make_project_bug_hunt_lane(change="external-client-notifications-pilot")
    adapter = MockCursorAdapter(
        MockScenario(
            name="dual-repr-hunt-repair",
            steps=[
                MockScenarioStep(hunt.review_prompt, raw),
                MockScenarioStep(hunt.repair_prompt, build_hunt_orch_clear_output()),
                MockScenarioStep(hunt.review_prompt, build_hunt_clean_output()),
            ],
        )
    )
    outcome = ReviewRepairLoop(adapter, lane=hunt).run()
    assert outcome.repair_rounds >= 1
    assert len(adapter.history) >= 2
    assert outcome.status == "HUNT_CLEAN"


def test_redundant_markdown_severity_conflict_is_invalid():
    raw = DUAL_REPR_FIXTURE.read_text(encoding="utf-8").replace(
        "**PBH-001 [MEDIUM]",
        "**PBH-001 [HIGH]",
        1,
    )
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID
    assert any("severity" in err and "PBH-001" in err for err in parsed.parse_errors)


def test_narrative_only_is_unparseable():
    parsed = parse_project_bug_hunt_result("I looked at the diff and everything seems fine.")
    assert parsed.completed is False
    assert parsed.clean is None
    assert parsed.parse_error_code == REVIEW_UNPARSEABLE
    assert parse_project_bug_hunt_result("").parse_error_code == REVIEW_UNPARSEABLE


def test_transient_transport_text_stays_transient():
    parsed = parse_project_bug_hunt_result("TRANSIENT_ERROR: CLI connection lost")
    assert parsed.transient_error is True
    assert parsed.parse_error_code is None
    assert classify_incomplete_review(parsed.to_dict())[0] == REVIEW_FAILED


def test_identical_parser_error_does_not_trigger_another_sdk_hunt():
    raw = _without_f2_block(_fixture_text())
    loop, adapter = _hunt_loop(raw)
    outcome = loop.run()

    assert outcome.status == REVIEW_CONTRACT_INVALID
    assert len(adapter.history) == 1
    assert outcome.review_runs == 1
    assert outcome.last_review is not None
    assert outcome.last_review.raw_output == raw
    assert outcome.to_dict()["last_review"]["raw_output_sha256"] == raw_output_sha256(raw)
    assert raw_output_sha256(raw) in outcome.message


def test_blocked_hunt_is_terminal_without_retry():
    loop, adapter = _hunt_loop("STATUS: BLOCKED_NEEDS_INPUT\nREASON: missing head\n")
    outcome = loop.run()
    assert outcome.status == REVIEW_BLOCKED
    assert len(adapter.history) == 1


def test_unparseable_hunt_is_terminal_without_retry():
    loop, adapter = _hunt_loop("I ran out of budget before writing findings.")
    outcome = loop.run()
    assert outcome.status == REVIEW_UNPARSEABLE
    assert len(adapter.history) == 1


def test_transient_hunt_failure_still_retries_once():
    loop, adapter = _hunt_loop(
        "STATUS: HUNT_COMPLETE\n- findings_total: 0\n",
        transient_failures=1,
    )
    outcome = loop.run()
    assert outcome.status == "HUNT_CLEAN"
    assert len(adapter.history) == 2


def test_markdown_hunt_findings_reach_repair_in_local_loop():
    loop, adapter = _hunt_loop(_fixture_text())
    review = loop.run_single_review()
    assert review.completed is True
    assert review.clean is False
    assert len(adapter.history) == 1


def test_bugbot_retry_rule_unchanged_for_legacy_parsers():
    parsed = parse_bugbot_result("no scope header here")
    assert parsed.completed is False
    assert parsed.parse_error_code is None
    assert parsed.review_status is None
    assert ReviewRepairLoop._should_retry_review(parsed, 0) is True


def test_run_artifact_review_evidence_keeps_exact_raw_output():
    raw = _without_f2_block(_fixture_text())
    review = parse_project_bug_hunt_result(raw).to_dict()
    payload = json.loads(json.dumps({"status": REVIEW_CONTRACT_INVALID, "last_review": review}))
    evidence = review_evidence_from_result(payload)
    assert evidence is not None
    assert evidence["raw_output"] == raw
    assert evidence["raw_output_sha256"] == raw_output_sha256(raw)
    assert evidence["parse_error_code"] == REVIEW_CONTRACT_INVALID
    assert review_evidence_from_result({"review_evidence": evidence}) == evidence
    assert review_evidence_from_result({"status": "HUNT_CLEAN"}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_status"),
    [
        ("project-bug-hunt-contract-invalid", REVIEW_CONTRACT_INVALID),
        ("project-bug-hunt-blocked", REVIEW_BLOCKED),
    ],
)
async def test_temporal_workflow_terminalizes_hunt_parse_outcomes(scenario: str, expected_status: str):
    from activities import verify_control_plane_worker_identity

    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario=scenario,
            lane="project-bug-hunt",
            base="main",
            head="HEAD",
            change="UNRESOLVED_CHANGE",
        )
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[ProjectBugHuntWorkflow],
            activities=[
                verify_control_plane_worker_identity,
                ensure_cursor_session,
                execute_project_bug_hunt,
                execute_project_bug_hunt_orch,
                execute_commit_materialize,
            ],
        ):
            workflow_input = {
                "mode": "mock",
                "repo_path": ".",
                "mock_scenario": scenario,
                "lane": "project-bug-hunt",
                "base": "main",
                "head": "HEAD",
                "change": "UNRESOLVED_CHANGE",
                "control_plane_code_identity": compute_control_plane_code_identity(),
                "expected_candidate_sha": "HEAD",
            }
            wf_id = f"workflow-{scenario}-test"
            from review_repair_terminal_policy import map_project_bug_hunt_terminal

            mapping = map_project_bug_hunt_terminal(expected_status)
            if mapping.temporal_execution_failed:
                with pytest.raises(WorkflowFailureError) as excinfo:
                    await execute_pbh_with_admission(
                        env.client,
                        workflow_input,
                        workflow_id=wf_id,
                        task_queue=TEMPORAL_TASK_QUEUE,
                    )
                result = terminal_payload_from_application_error(excinfo.value.cause) or {}
            else:
                result = await execute_pbh_with_admission(
                    env.client,
                    workflow_input,
                    workflow_id=wf_id,
                    task_queue=TEMPORAL_TASK_QUEUE,
                )

    assert result["status"] == expected_status
    assert result["review_runs"] == 1
    assert result["repair_rounds"] == 0
    last_review = result["last_review"]
    assert last_review["raw_output_sha256"] == raw_output_sha256(last_review["raw_output"])
    if expected_status == REVIEW_CONTRACT_INVALID:
        assert last_review["raw_output"] == build_hunt_contract_invalid_output()
        assert last_review["parse_errors"] == ["findings_total=2 but parsed 1 finding block(s)"]


def test_hunt_pipeline_treats_new_review_outcomes_as_terminal():
    spec = json.loads((FIXTURE.parents[2] / "pipelines.json").read_text(encoding="utf-8"))[
        "pipelines"
    ]["project-bug-hunt"]
    assert is_terminal_outcome(REVIEW_UNPARSEABLE, spec)
    assert classify_terminal_bucket(REVIEW_UNPARSEABLE, spec) == "FAIL"
    assert classify_terminal_bucket(REVIEW_CONTRACT_INVALID, spec) == "FAIL"
    assert classify_terminal_bucket(REVIEW_BLOCKED, spec) == "BLOCKED"


def test_review_prompt_requires_plain_machine_block():
    assert "no markdown bold" in PROJECT_BUG_HUNT_REVIEW_PROMPT
    assert "`STATUS: HUNT_COMPLETE`" in PROJECT_BUG_HUNT_REVIEW_PROMPT
    assert "### FINDING_ID:" in PROJECT_BUG_HUNT_REVIEW_PROMPT
    assert "one canonical" in PROJECT_BUG_HUNT_REVIEW_PROMPT
    assert "must not repeat machine-parseable finding headers" in PROJECT_BUG_HUNT_REVIEW_PROMPT
