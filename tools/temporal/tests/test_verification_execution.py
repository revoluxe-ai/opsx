"""Mock scenarios for VerificationExecutionWorkflow (in-process + Temporal)."""

from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities import (
    ActivityConfig,
    configure_activities,
    ensure_verification_context,
    execute_assigned_check,
    execute_next_check,
)
from config import TEMPORAL_TASK_QUEUE
from tempo_operator import (
    build_start_cmd,
    get_pipeline,
    parse_verification_cli_tokens,
    resolve_verification_args,
)
from verification_engine import (
    TEMPORAL_COMPLETE,
    TERMINAL_BLOCKED_CHECK,
    TERMINAL_FAIL,
    TERMINAL_SUCCESS,
    TERMINAL_UNCERTAIN,
    AssignedCheckResult,
    NextCheckResult,
    build_execute_prompt,
    build_next_check_prompt,
    should_rerun_blocked_after_smoke_ack,
    should_stop_on_evidence_gap,
    should_stop_on_prior_fail_blocked,
)
from owner_browser_check_gate import write_go_ack
from verification_loop import VerificationExecutionLoop
from verification_mock import VerificationMockHarness, clear_mock_harnesses
from workflow import VerificationExecutionWorkflow


def _run_scenario(tmp_path: Path, scenario: str, change: str = "mock-change"):
    clear_mock_harnesses()
    harness = VerificationMockHarness.from_scenario(
        scenario, change=change, repo=tmp_path
    )
    return VerificationExecutionLoop(
        change=change,
        run_next_check=harness.run_next_check,
        run_assigned_check=harness.run_assigned_check,
    ).run(), harness


def test_build_prompts():
    assert build_next_check_prompt("my-change") == "/opsx-next-check change=my-change"
    assignment = {
        "executor": "opsx-run-tests",
        "executor_mode": "bounded",
        "change_id": "c",
        "plan_id": "p",
        "required_check_id": "c×frontend_tests",
        "target_id": "t",
        "capability_id": "frontend_tests",
        "binding_fingerprint": "fp",
        "verification_frontier_hash": "fh",
        "implementation_snapshot": {
            "provenance_head": "h",
            "content_digest": "d",
            "content_paths": ["a"],
        },
        "bindings_path": "openspec/changes/c/verify/EXECUTOR-BINDINGS.json",
        "expected_result_path": "openspec/changes/c/verify/check-results/p/x.json",
        "assignment_epoch": 1,
    }
    prompt = build_execute_prompt(assignment)
    assert prompt.startswith("/opsx-run-tests\n")
    assert "mode=bounded" in prompt
    assert "required_check_id=c×frontend_tests" in prompt
    assert "assignment_epoch=1" in prompt


def test_verification_pipeline_spec():
    spec = get_pipeline("verification")
    assert spec["command"] == "tempo-verification"
    assert spec["lane"] == "verification"
    assert spec["workflow_type"] == "VerificationExecutionWorkflow"
    assert spec["clean_status"] == "ALL_REQUIRED_CHECKS_PASS"
    assert spec["requires"] == ["change"]
    assert spec["session_dependency"] == "NONE"
    assert spec["auto_repair"] is False
    assert spec.get("pre_assignment_steps") == []
    assert "OWNER_CHROME_FOCUS_GATE" not in (spec.get("process_fail_statuses") or [])


def test_parse_verification_tokens():
    parsed = parse_verification_cli_tokens(
        ["change=my-change", "low", "medium"]
    )
    assert parsed == {
        "review": "low",
        "repair": "medium",
        "change": "my-change",
        "base": None,
    }


def test_parse_verification_rejects_base_head():
    with pytest.raises(ValueError, match="does not accept base"):
        parse_verification_cli_tokens(["change=x", "base=stage", "low", "low"])
    with pytest.raises(ValueError, match="does not accept head"):
        parse_verification_cli_tokens(["change=x", "head=foo", "low", "low"])


def test_resolve_verification_args():
    resolved = resolve_verification_args(Path("."), change="my-change")
    assert resolved == {"change": "my-change"}


def test_build_start_cmd_verification_no_base_head(tmp_path: Path):
    spec = get_pipeline("verification")
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    cmd = build_start_cmd(
        spec,
        repo=tmp_path,
        overlay=overlay,
        review="low",
        repair="low",
        resolved={"change": "my-change"},
    )
    assert cmd[cmd.index("--lane") + 1] == "verification"
    assert "--change" in cmd and cmd[cmd.index("--change") + 1] == "my-change"
    assert "--base" not in cmd
    assert "--head" not in cmd


def test_scenario1_all_pass(tmp_path: Path):
    outcome, harness = _run_scenario(tmp_path, "verification-all-pass")
    assert outcome.status == TERMINAL_SUCCESS
    assert outcome.temporal_status == TEMPORAL_COMPLETE
    assert outcome.progress_pass == 3
    assert outcome.progress_total == 3
    assert harness.executor_calls == 3
    assert harness.next_check_calls >= 4


def test_scenario2_second_fail(tmp_path: Path):
    outcome, harness = _run_scenario(tmp_path, "verification-second-fail")
    assert outcome.status == TERMINAL_FAIL
    assert outcome.progress_pass == 1
    assert outcome.progress_total == 3
    assert outcome.failed_required_check_id == "check-b"
    assert harness.executor_calls == 2  # C never executed


def test_scenario3_executor_blocked(tmp_path: Path):
    outcome, harness = _run_scenario(tmp_path, "verification-executor-blocked")
    assert outcome.status == TERMINAL_BLOCKED_CHECK
    assert outcome.result_status == "BLOCKED"
    assert harness.executor_calls == 1
    assert outcome.blocked_required_check_id == "check-a"


def test_scenario4_snapshot_drift(tmp_path: Path):
    outcome, harness = _run_scenario(tmp_path, "verification-snapshot-drift")
    assert outcome.status == "BLOCKED_SNAPSHOT_DRIFT"
    assert harness.executor_calls == 0


def test_scenario5_invalid_evidence(tmp_path: Path):
    outcome, harness = _run_scenario(tmp_path, "verification-invalid-evidence")
    assert outcome.status == "EVIDENCE_INVALID"
    assert harness.executor_calls == 1
    assert outcome.progress_pass == 0


def test_scenario6_next_check_transient(tmp_path: Path):
    outcome, harness = _run_scenario(tmp_path, "verification-next-check-transient")
    assert outcome.status == TERMINAL_SUCCESS
    assert harness.executor_calls == 1
    # one technical failure + successful next-checks
    assert harness.next_check_calls >= 3


def test_scenario7_executor_lost_but_result(tmp_path: Path):
    outcome, harness = _run_scenario(
        tmp_path, "verification-executor-lost-but-result"
    )
    assert outcome.status == TERMINAL_SUCCESS
    assert harness.executor_calls == 1  # not executed twice


def test_scenario8_executor_uncertain(tmp_path: Path):
    outcome, harness = _run_scenario(tmp_path, "verification-executor-uncertain")
    assert outcome.status == TERMINAL_UNCERTAIN
    assert harness.executor_calls == 1
    assert harness.next_check_calls == 1  # no reconcile after uncertain stop


def test_scenario9_ten_checks(tmp_path: Path):
    outcome, harness = _run_scenario(tmp_path, "verification-ten-checks")
    assert outcome.status == TERMINAL_SUCCESS
    assert outcome.progress_pass == 10
    assert outcome.progress_total == 10
    assert harness.executor_calls == 10


def _check_required(*, check_id: str, evidence_missing: bool = True) -> NextCheckResult:
    return NextCheckResult(
        completed=True,
        status="CHECK_REQUIRED",
        progress_pass=1,
        progress_total=10,
        current_required_check_id=check_id,
        assignment_epoch=2,
        execute_assignment={"required_check_id": check_id, "expected_result_path": "x.json"},
        current_last_result=None,
        block_reason=None,
        plan_id="vp-test",
        ledger_path=None,
        raw_output="STATUS: CHECK_REQUIRED\nREASON: EVIDENCE_MISSING",
        evidence_missing=evidence_missing,
        evidence_invalid=False,
    )


def test_evidence_gap_fresh_next_check_is_not_terminal():
    """Real next-check writes EVIDENCE_MISSING when assigning the next check."""
    nxt = _check_required(check_id="check-b")
    assert should_stop_on_evidence_gap(nxt, last_executed_required_check_id="check-a") is False
    assert should_stop_on_evidence_gap(nxt, last_executed_required_check_id=None) is False


def test_evidence_gap_same_check_after_executor_is_terminal():
    nxt = _check_required(check_id="check-a")
    nxt.evidence_invalid = True
    nxt.evidence_missing = False
    nxt.raw_output = "STATUS: CHECK_REQUIRED\nEVIDENCE_INVALID"
    assert should_stop_on_evidence_gap(nxt, last_executed_required_check_id="check-a") is True


def _smoke_blocked_next_check(
    *,
    result_path: Path,
    check_id: str = "mock×smoke_acceptance",
    epoch: int = 3,
) -> NextCheckResult:
    assignment = {
        "required_check_id": check_id,
        "capability_id": "smoke_acceptance",
        "executor": "opsx-browser-check",
        "assignment_epoch": epoch,
        "expected_result_path": str(result_path),
        "change_id": "mock-change",
        "plan_id": "vp-mock",
        "target_id": "mock.target",
        "binding_fingerprint": "fp",
        "verification_frontier_hash": "fh",
        "executor_mode": "bounded",
        "implementation_snapshot": {},
        "bindings_path": "openspec/changes/mock-change/verify/EXECUTOR-BINDINGS.json",
    }
    return NextCheckResult(
        completed=True,
        status="CHECK_REQUIRED",
        progress_pass=2,
        progress_total=10,
        current_required_check_id=check_id,
        assignment_epoch=epoch,
        execute_assignment=assignment,
        current_last_result="BLOCKED",
        block_reason=None,
        plan_id="vp-mock",
        ledger_path=None,
        raw_output="STATUS: CHECK_REQUIRED",
    )


def test_rerun_blocked_when_go_ack_newer_than_result(tmp_path: Path):
    result_path = tmp_path / "blocked.json"
    result_path.write_text('{"STATUS":"BLOCKED"}', encoding="utf-8")
    nxt = _smoke_blocked_next_check(result_path=result_path)
    assert should_rerun_blocked_after_smoke_ack(repo=tmp_path, next_check=nxt) is False

    write_go_ack(
        tmp_path,
        required_check_id=nxt.current_required_check_id or "",
        assignment_epoch=nxt.assignment_epoch or 0,
        change="mock-change",
    )
    assert should_rerun_blocked_after_smoke_ack(repo=tmp_path, next_check=nxt) is True
    assert should_stop_on_prior_fail_blocked(repo=tmp_path, next_check=nxt) is False


def test_stop_on_blocked_without_go_ack(tmp_path: Path):
    result_path = tmp_path / "blocked.json"
    result_path.write_text('{"STATUS":"BLOCKED"}', encoding="utf-8")
    nxt = _smoke_blocked_next_check(result_path=result_path)
    assert should_stop_on_prior_fail_blocked(repo=tmp_path, next_check=nxt) is True


def test_loop_reruns_smoke_executor_after_go_ack(tmp_path: Path):
    result_path = tmp_path / "verify" / "smoke.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text('{"STATUS":"BLOCKED"}', encoding="utf-8")
    check_id = "mock×smoke_acceptance"
    epoch = 3
    write_go_ack(
        tmp_path,
        required_check_id=check_id,
        assignment_epoch=epoch,
        change="mock-change",
    )
    blocked_nc = _smoke_blocked_next_check(
        result_path=result_path, check_id=check_id, epoch=epoch
    )
    success_nc = NextCheckResult(
        completed=True,
        status=TERMINAL_SUCCESS,
        progress_pass=10,
        progress_total=10,
        current_required_check_id=None,
        assignment_epoch=None,
        execute_assignment=None,
        current_last_result=None,
        block_reason=None,
        plan_id="vp-mock",
        ledger_path=None,
        raw_output=f"STATUS: {TERMINAL_SUCCESS}",
    )
    calls = {"next": 0, "exec": 0}

    def run_next_check() -> NextCheckResult:
        calls["next"] += 1
        return blocked_nc if calls["next"] == 1 else success_nc

    def run_assigned_check(assignment: dict) -> AssignedCheckResult:
        calls["exec"] += 1
        return AssignedCheckResult(
            completed=True,
            uncertain=False,
            durable_result_found=True,
            durable_result_status="PASS",
            expected_result_path=assignment.get("expected_result_path"),
            executor=assignment.get("executor"),
            required_check_id=assignment.get("required_check_id"),
            raw_output="STATUS: PASS",
        )

    outcome = VerificationExecutionLoop(
        change="mock-change",
        repo=str(tmp_path),
        run_next_check=run_next_check,
        run_assigned_check=run_assigned_check,
    ).run()

    assert calls["exec"] == 1
    assert outcome.status == TERMINAL_SUCCESS
    assert outcome.executor_runs == 1


def test_scenario1_survives_evidence_missing_on_next_assignment(tmp_path: Path):
    """Regression: first PASS then next-check EVIDENCE_MISSING must still run check 2+."""
    outcome, harness = _run_scenario(tmp_path, "verification-all-pass")
    assert outcome.status == TERMINAL_SUCCESS
    assert harness.executor_calls == 3
    assert outcome.progress_pass == 3


@pytest.mark.asyncio
async def test_temporal_verification_workflow_all_pass(tmp_path: Path):
    clear_mock_harnesses()
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=str(tmp_path),
            mock_scenario="verification-all-pass",
            change="mock-change",
            lane="verification",
        )
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[VerificationExecutionWorkflow],
            activities=[
                ensure_verification_context,
                execute_next_check,
                execute_assigned_check,
            ],
        ):
            result = await env.client.execute_workflow(
                VerificationExecutionWorkflow.run,
                {
                    "mode": "mock",
                    "repo_path": str(tmp_path),
                    "mock_scenario": "verification-all-pass",
                    "lane": "verification",
                    "change": "mock-change",
                },
                id="verification-workflow-all-pass",
                task_queue=TEMPORAL_TASK_QUEUE,
            )
    assert result["status"] == TERMINAL_SUCCESS
    assert result["temporal_status"] == TEMPORAL_COMPLETE
    assert result["progress_pass"] == 3
    assert result["executor_runs"] == 3
    assert result["VERIFICATION_SESSION_DEPENDENCY"] == "NONE"


@pytest.mark.asyncio
async def test_temporal_verification_workflow_second_fail(tmp_path: Path):
    clear_mock_harnesses()
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=str(tmp_path),
            mock_scenario="verification-second-fail",
            change="mock-change",
            lane="verification",
        )
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[VerificationExecutionWorkflow],
            activities=[
                ensure_verification_context,
                execute_next_check,
                execute_assigned_check,
            ],
        ):
            result = await env.client.execute_workflow(
                VerificationExecutionWorkflow.run,
                {
                    "mode": "mock",
                    "repo_path": str(tmp_path),
                    "mock_scenario": "verification-second-fail",
                    "lane": "verification",
                    "change": "mock-change",
                },
                id="verification-workflow-second-fail",
                task_queue=TEMPORAL_TASK_QUEUE,
            )
    assert result["status"] == TERMINAL_FAIL
    assert result["progress_pass"] == 1
    assert result["executor_runs"] == 2
    assert result["failed_required_check_id"] == "check-b"
