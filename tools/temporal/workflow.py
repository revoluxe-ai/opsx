"""Temporal workflows orchestrating separate Cursor review/repair agent runs.

BugbotRepairWorkflow keeps its registered name and activity names so in-flight
and UI history stay valid. SecurityReviewWorkflow and ProjectBugHuntWorkflow
reuse the same loop with lane-specific activities.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import WorkflowRunConfig
    from commit_materialization import COMMIT_MATERIALIZE_BLOCKED
    from config import MAX_REPAIR_ROUNDS, MAX_REVIEW_RUNS
    from loop_engine import ReviewRepairLoop
    from owner_chrome_focus_gate import TERMINAL_STATUS as OWNER_CHROME_FOCUS_GATE
    from session_lifecycle_contract import hunt_heartbeat_timeout, hunt_start_to_close_timeout
    from verification_engine import (
        TEMPORAL_COMPLETE,
        TERMINAL_SUCCESS,
        TERMINAL_UNCERTAIN,
        NextCheckResult,
        classify_after_executor,
        should_stop_on_evidence_gap,
        workflow_stop_on_prior_fail_blocked,
    )


async def _run_review_repair_loop(
    config: WorkflowRunConfig,
    *,
    review_activity: str,
    repair_activity: str,
    clean_status: str,
    review_start_to_close_timeout: timedelta | None = None,
    review_heartbeat_timeout: timedelta | None = None,
    repair_start_to_close_timeout: timedelta | None = None,
    repair_heartbeat_timeout: timedelta | None = None,
    materialize_start_to_close_timeout: timedelta | None = None,
    materialize_heartbeat_timeout: timedelta | None = None,
) -> dict:
    review_stc = review_start_to_close_timeout or timedelta(hours=3)
    review_hb = review_heartbeat_timeout or timedelta(minutes=15)
    repair_stc = repair_start_to_close_timeout or timedelta(hours=6)
    repair_hb = repair_heartbeat_timeout or timedelta(minutes=30)
    materialize_stc = materialize_start_to_close_timeout or timedelta(hours=2)
    materialize_hb = materialize_heartbeat_timeout or timedelta(minutes=15)
    session = await workflow.execute_activity(
        "ensure_cursor_session",
        config.to_dict(),
        start_to_close_timeout=timedelta(minutes=5),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )

    repair_rounds = 0
    review_runs = 0
    last_fp: str | None = None
    consecutive_same_fp_reviews = 0
    last_review = None
    last_repair = None
    last_materialize = None
    candidate_sha = config.head

    def _terminal(status: str, message: str) -> dict:
        sha = None
        if isinstance(last_review, dict):
            sha = last_review.get("candidate_sha")
        return {
            "status": status,
            "repair_rounds": repair_rounds,
            "review_runs": review_runs,
            "session_id": session.get("session_id"),
            "message": message,
            "last_review": last_review,
            "last_repair": last_repair,
            "last_materialize": last_materialize,
            "candidate_sha": sha or candidate_sha,
        }

    while True:
        review_runs += 1
        review = await workflow.execute_activity(
            review_activity,
            {
                "review_number": review_runs,
                "mock_scenario": config.mock_scenario,
                "session_id": session.get("session_id"),
                "lane": config.lane,
                "base": config.base,
                "head": config.head,
                "change": config.change,
            },
            start_to_close_timeout=review_stc,
            heartbeat_timeout=review_hb,
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(seconds=10),
                backoff_coefficient=2.0,
            ),
        )
        last_review = review
        if review.get("candidate_sha"):
            candidate_sha = review.get("candidate_sha")

        if not review.get("completed"):
            return _terminal(
                "REVIEW_FAILED",
                f"Review did not complete: {review.get('raw_output', '')[:300]}",
            )

        if review.get("clean"):
            message = "Review reported no actionable findings."
            excluded = review.get("excluded_findings") or []
            repairable = review.get("repairable_findings") or []
            if excluded and not repairable:
                message = (
                    "Product lane clean after POC filter; "
                    f"{len(excluded)} process finding(s) excluded from automation."
                )
            return _terminal(clean_status, message)

        if repair_rounds >= MAX_REPAIR_ROUNDS:
            return _terminal(
                "MAX_ROUNDS_REACHED",
                (
                    f"Final review after repair #{repair_rounds} "
                    "still has actionable findings."
                ),
            )

        last_fp, consecutive_same_fp_reviews = ReviewRepairLoop.update_stagnation_state(
            review.get("fingerprint"),
            last_fp=last_fp,
            consecutive_same_fp_reviews=consecutive_same_fp_reviews,
        )
        if ReviewRepairLoop.is_stalled(repair_rounds, consecutive_same_fp_reviews):
            return _terminal(
                "STALLED",
                "Actionable findings fingerprint unchanged across repair rounds.",
            )

        if review_runs >= MAX_REVIEW_RUNS:
            return _terminal(
                "MAX_ROUNDS_REACHED",
                "Maximum review run budget reached.",
            )

        repair = await workflow.execute_activity(
            repair_activity,
            {
                "repair_number": repair_rounds + 1,
                "mock_scenario": config.mock_scenario,
                "session_id": session.get("session_id"),
                "lane": config.lane,
                "base": config.base,
                "head": config.head,
                "change": config.change,
            },
            start_to_close_timeout=repair_stc,
            heartbeat_timeout=repair_hb,
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        last_repair = repair
        repair_rounds += 1

        if repair.get("uncertain"):
            return _terminal(
                "REPAIR_COMPLETION_UNCERTAIN",
                "Repair invocation ended ambiguously after possible side effects.",
            )

        if not repair.get("completed"):
            return _terminal(
                "REPAIR_FAILED",
                f"Repair did not reach terminal status: {repair.get('raw_output', '')[:300]}",
            )

        if repair.get("repaired_this_batch") is False:
            return _terminal(
                "NO_REPAIR_PROGRESS",
                "Orch completed with nothing repaired this batch; stopping loop.",
            )

        last_materialize = await workflow.execute_activity(
            "execute_commit_materialize",
            {
                "repair": repair,
                "mock_scenario": config.mock_scenario,
                "session_id": session.get("session_id"),
                "lane": config.lane,
                "base": config.base,
                "head": config.head,
                "change": config.change,
            },
            start_to_close_timeout=materialize_stc,
            heartbeat_timeout=materialize_hb,
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
        if not last_materialize.get("ok"):
            return _terminal(
                COMMIT_MATERIALIZE_BLOCKED,
                (
                    "Post-repair /opsx-commit-all GO did not produce a clean "
                    "committed candidate; next review is forbidden. "
                    f"reason={last_materialize.get('reason')}"
                ),
            )
        if last_materialize.get("head_after"):
            config.head = last_materialize["head_after"]
            candidate_sha = last_materialize["head_after"]
        if last_materialize.get("candidate_transitioned"):
            last_fp = None
            consecutive_same_fp_reviews = 0


@workflow.defn(name="BugbotRepairWorkflow")
class BugbotRepairWorkflow:
    """
    Orchestrates one Cursor prompt per activity so each review/repair step is
    visible separately in Temporal UI.
    """

    @workflow.run
    async def run(self, config: WorkflowRunConfig | dict) -> dict:
        if isinstance(config, dict):
            config = WorkflowRunConfig(**config)
        return await _run_review_repair_loop(
            config,
            review_activity="execute_bugbot_review",
            repair_activity="execute_opsx_repair",
            clean_status="BUGBOT_CLEAN",
        )


@workflow.defn(name="SecurityReviewWorkflow")
class SecurityReviewWorkflow:
    """Same loop as bugbot; `/review-security` skill + `/opsx-bug-team`."""

    @workflow.run
    async def run(self, config: WorkflowRunConfig | dict) -> dict:
        if isinstance(config, dict):
            config = WorkflowRunConfig(**config)
        return await _run_review_repair_loop(
            config,
            review_activity="execute_security_review",
            repair_activity="execute_security_repair",
            clean_status="SECURITY_CLEAN",
        )


@workflow.defn(name="ProjectBugHuntWorkflow")
class ProjectBugHuntWorkflow:
    """Hunt → orch-bug-fix cycle with CLI tokens base/head/change."""

    @workflow.run
    async def run(self, config: WorkflowRunConfig | dict) -> dict:
        if isinstance(config, dict):
            config = WorkflowRunConfig(**config)
        return await _run_review_repair_loop(
            config,
            review_activity="execute_project_bug_hunt",
            repair_activity="execute_project_bug_hunt_orch",
            clean_status="HUNT_CLEAN",
            review_start_to_close_timeout=hunt_start_to_close_timeout(),
            review_heartbeat_timeout=hunt_heartbeat_timeout(),
            repair_start_to_close_timeout=hunt_start_to_close_timeout(),
            repair_heartbeat_timeout=hunt_heartbeat_timeout(),
        )


@workflow.defn(name="VerificationExecutionWorkflow")
class VerificationExecutionWorkflow:
    """Scheduler → assigned executor → scheduler. No repair. FAIL/BLOCKED stop.

    VERIFICATION_SESSION_DEPENDENCY: NONE — durable ledger/plan/bindings/result
    are authority; each activity uses a fresh Cursor context when real.
    """

    @workflow.run
    async def run(self, config: WorkflowRunConfig | dict) -> dict:
        if isinstance(config, dict):
            config = WorkflowRunConfig(**config)

        change = (config.change or "").strip()
        if not change:
            return {
                "status": "CONTRACT_ERROR",
                "message": "verification lane requires --change",
                "change": change,
                "VERIFICATION_SESSION_DEPENDENCY": "NONE",
            }

        ctx = await workflow.execute_activity(
            "ensure_verification_context",
            {
                "change": change,
                "repo_path": config.repo_path,
                "mock_scenario": config.mock_scenario,
                "lane": "verification",
            },
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        if ctx.get("status") not in (None, "OK", "READY"):
            return {
                "status": ctx.get("status") or "BLOCKED",
                "change": change,
                "message": ctx.get("message") or "verification context not ready",
                "block_reason": ctx.get("block_reason"),
                "ledger_path": ctx.get("ledger_path"),
                "VERIFICATION_SESSION_DEPENDENCY": "NONE",
                **{k: v for k, v in ctx.items() if k not in ("status", "message")},
            }

        next_check_runs = 0
        executor_runs = 0
        last_next: dict | None = None
        last_assigned: dict | None = None
        max_executor_runs = 64
        next_check_technical_retries = 2

        while True:
            attempts = 0
            next_check: dict
            while True:
                next_check = await workflow.execute_activity(
                    "execute_next_check",
                    {
                        "change": change,
                        "repo_path": config.repo_path,
                        "mock_scenario": config.mock_scenario,
                        "lane": "verification",
                        "next_check_number": next_check_runs + 1,
                    },
                    start_to_close_timeout=timedelta(hours=2),
                    heartbeat_timeout=timedelta(minutes=15),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                if next_check.get("completed") or next_check.get("status") != (
                    "NEXT_CHECK_TECHNICAL_FAILURE"
                ):
                    break
                attempts += 1
                if attempts > next_check_technical_retries:
                    break
            next_check_runs += 1
            last_next = next_check

            if not next_check.get("completed"):
                return {
                    "status": "NEXT_CHECK_TECHNICAL_FAILURE",
                    "change": change,
                    "plan_id": next_check.get("plan_id"),
                    "progress_pass": next_check.get("progress_pass", 0),
                    "progress_total": next_check.get("progress_total", 0),
                    "progress": (
                        f"{next_check.get('progress_pass', 0)}/"
                        f"{next_check.get('progress_total', 0)}"
                    ),
                    "next_check_runs": next_check_runs,
                    "executor_runs": executor_runs,
                    "message": "next-check failed after technical retries",
                    "last_next_check": next_check,
                    "last_assigned_check": last_assigned,
                    "block_reason": next_check.get("block_reason"),
                    "ledger_path": next_check.get("ledger_path"),
                    "VERIFICATION_SESSION_DEPENDENCY": "NONE",
                }

            nc = NextCheckResult(
                completed=bool(next_check.get("completed")),
                status=str(next_check.get("status") or ""),
                progress_pass=int(next_check.get("progress_pass") or 0),
                progress_total=int(next_check.get("progress_total") or 0),
                current_required_check_id=next_check.get("current_required_check_id"),
                assignment_epoch=next_check.get("assignment_epoch"),
                execute_assignment=next_check.get("execute_assignment"),
                current_last_result=next_check.get("current_last_result"),
                block_reason=next_check.get("block_reason"),
                plan_id=next_check.get("plan_id"),
                ledger_path=next_check.get("ledger_path"),
                raw_output=next_check.get("raw_output") or "",
                evidence_missing=bool(next_check.get("evidence_missing")),
                evidence_invalid=bool(next_check.get("evidence_invalid")),
                rerun_blocked_smoke_ack=bool(
                    next_check.get("rerun_blocked_smoke_ack")
                ),
            )

            if nc.status == TERMINAL_SUCCESS:
                return {
                    "status": TERMINAL_SUCCESS,
                    "temporal_status": TEMPORAL_COMPLETE,
                    "change": change,
                    "plan_id": nc.plan_id,
                    "progress_pass": nc.progress_pass,
                    "progress_total": nc.progress_total,
                    "progress": f"{nc.progress_pass}/{nc.progress_total}",
                    "next_check_runs": next_check_runs,
                    "executor_runs": executor_runs,
                    "message": "All frozen required checks have valid PASS evidence",
                    "last_next_check": next_check,
                    "last_assigned_check": last_assigned,
                    "next_action": f"/opsx-verify change={change} mode=archive",
                    "ledger_path": nc.ledger_path,
                    "VERIFICATION_SESSION_DEPENDENCY": "NONE",
                }

            terminal = classify_after_executor(next_check=nc)
            if terminal is not None and nc.status != "CHECK_REQUIRED":
                return _verification_terminal(
                    terminal,
                    change=change,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    last_next=next_check,
                    last_assigned=last_assigned,
                )
            if terminal is not None and workflow_stop_on_prior_fail_blocked(nc):
                return _verification_terminal(
                    terminal,
                    change=change,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    last_next=next_check,
                    last_assigned=last_assigned,
                )
            # Evidence gaps are terminal only after an executor attempt for the
            # *current* assignment. Fresh CHECK_REQUIRED for the next check
            # mentions EVIDENCE_MISSING before that check's executor has run.
            last_executed_id = (
                last_assigned.get("required_check_id") if last_assigned else None
            )
            if terminal is not None and should_stop_on_evidence_gap(nc, last_executed_id):
                return _verification_terminal(
                    terminal,
                    change=change,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    last_next=next_check,
                    last_assigned=last_assigned,
                )

            if nc.status != "CHECK_REQUIRED" or not nc.execute_assignment:
                return {
                    "status": nc.status or "BLOCKED",
                    "change": change,
                    "plan_id": nc.plan_id,
                    "progress_pass": nc.progress_pass,
                    "progress_total": nc.progress_total,
                    "progress": f"{nc.progress_pass}/{nc.progress_total}",
                    "next_check_runs": next_check_runs,
                    "executor_runs": executor_runs,
                    "message": "next-check did not yield an executable assignment",
                    "last_next_check": next_check,
                    "last_assigned_check": last_assigned,
                    "block_reason": nc.block_reason,
                    "ledger_path": nc.ledger_path,
                    "VERIFICATION_SESSION_DEPENDENCY": "NONE",
                }

            if executor_runs >= max_executor_runs:
                return {
                    "status": "MAX_EXECUTOR_RUNS_REACHED",
                    "change": change,
                    "plan_id": nc.plan_id,
                    "progress_pass": nc.progress_pass,
                    "progress_total": nc.progress_total,
                    "next_check_runs": next_check_runs,
                    "executor_runs": executor_runs,
                    "message": "Executor run budget exceeded (safety stop)",
                    "last_next_check": next_check,
                    "last_assigned_check": last_assigned,
                    "VERIFICATION_SESSION_DEPENDENCY": "NONE",
                }

            assignment = dict(nc.execute_assignment)
            assigned = await workflow.execute_activity(
                "execute_assigned_check",
                {
                    "change": change,
                    "repo_path": config.repo_path,
                    "mock_scenario": config.mock_scenario,
                    "lane": "verification",
                    "assignment": assignment,
                    "executor_number": executor_runs + 1,
                },
                start_to_close_timeout=timedelta(hours=6),
                heartbeat_timeout=timedelta(minutes=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            last_assigned = assigned

            if assigned.get("owner_chrome_focus_gate_pending"):
                gate = assigned.get("owner_chrome_focus_gate") or {}
                return {
                    "status": OWNER_CHROME_FOCUS_GATE,
                    "change": change,
                    "plan_id": nc.plan_id,
                    "progress_pass": nc.progress_pass,
                    "progress_total": nc.progress_total,
                    "progress": f"{nc.progress_pass}/{nc.progress_total}",
                    "next_check_runs": next_check_runs,
                    "executor_runs": executor_runs,
                    "message": "Owner must focus game Chrome before smoke_acceptance",
                    "last_next_check": next_check,
                    "last_assigned_check": assigned,
                    "blocked_required_check_id": assignment.get("required_check_id"),
                    "capability_id": assignment.get("capability_id"),
                    "executor": assignment.get("executor"),
                    "block_reason": "owner_chrome_focus_required",
                    "next_action": gate.get("resume_command"),
                    "owner_chrome_focus_gate": gate,
                    "gate_page_url": gate.get("gate_page_url"),
                    "ledger_path": nc.ledger_path,
                    "VERIFICATION_SESSION_DEPENDENCY": "NONE",
                }

            executor_runs += 1

            if assigned.get("uncertain") and not assigned.get("durable_result_found"):
                return {
                    "status": TERMINAL_UNCERTAIN,
                    "change": change,
                    "plan_id": nc.plan_id,
                    "progress_pass": nc.progress_pass,
                    "progress_total": nc.progress_total,
                    "progress": f"{nc.progress_pass}/{nc.progress_total}",
                    "next_check_runs": next_check_runs,
                    "executor_runs": executor_runs,
                    "message": (
                        "Executor completion uncertain and no durable "
                        "BOUNDED_CHECK_RESULT"
                    ),
                    "last_next_check": next_check,
                    "last_assigned_check": assigned,
                    "failed_required_check_id": assignment.get("required_check_id"),
                    "result_path": assignment.get("expected_result_path"),
                    "executor": assignment.get("executor"),
                    "next_action": (
                        "Inspect expected_result_path; do not blind-retry executor"
                    ),
                    "VERIFICATION_SESSION_DEPENDENCY": "NONE",
                }

            # Always reconcile via next-check before deciding progress.
            continue


def _verification_terminal(
    terminal: dict,
    *,
    change: str,
    next_check_runs: int,
    executor_runs: int,
    last_next: dict,
    last_assigned: dict | None,
) -> dict:
    status = str(terminal["status"])
    progress_pass = int(terminal.get("progress_pass") or last_next.get("progress_pass") or 0)
    progress_total = int(
        terminal.get("progress_total") or last_next.get("progress_total") or 0
    )
    return {
        "status": status,
        "temporal_status": terminal.get("temporal_status"),
        "change": change,
        "plan_id": terminal.get("plan_id") or last_next.get("plan_id"),
        "progress_pass": progress_pass,
        "progress_total": progress_total,
        "progress": f"{progress_pass}/{progress_total}",
        "next_check_runs": next_check_runs,
        "executor_runs": executor_runs,
        "message": status,
        "last_next_check": last_next,
        "last_assigned_check": last_assigned,
        "failed_required_check_id": terminal.get("failed_required_check_id"),
        "blocked_required_check_id": terminal.get("blocked_required_check_id"),
        "result_path": terminal.get("result_path"),
        "result_status": terminal.get("result_status"),
        "target_id": terminal.get("target_id"),
        "capability_id": terminal.get("capability_id"),
        "executor": terminal.get("executor"),
        "next_action": terminal.get("next_action"),
        "block_reason": terminal.get("block_reason"),
        "ledger_path": terminal.get("ledger_path") or last_next.get("ledger_path"),
        "VERIFICATION_SESSION_DEPENDENCY": "NONE",
    }


@workflow.defn(name="CursorSdkExecutionWorkflow")
class CursorSdkExecutionWorkflow:
    """Canonical OPSX / Temporal → CursorExecutor → Cursor SDK execution hop.

    Workflow schedules execute_cursor only; it does not treat agent prose as
    engineering PASS or verification GREEN.
    """

    @workflow.run
    async def run(self, payload: dict) -> dict:
        orchestration_id = str(
            payload.get("orchestration_execution_id") or payload.get("execution_id") or ""
        )
        result = await workflow.execute_activity(
            "execute_cursor",
            payload,
            start_to_close_timeout=timedelta(hours=2),
            heartbeat_timeout=timedelta(minutes=30),
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                initial_interval=timedelta(seconds=5),
                backoff_coefficient=2.0,
            ),
        )
        return {
            "orchestration_execution_id": orchestration_id,
            "execution_status": result.get("execution_status"),
            "cursor_execution": result,
            "workflow_semantics": "EXECUTION_TRANSPORT_ONLY",
            "engineering_pass_inferred": False,
        }
