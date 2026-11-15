"""In-process verification execution loop (Temporal workflow twin).

Scheduler → assigned executor → scheduler. No repair. FAIL/BLOCKED stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from owner_chrome_focus_gate import TERMINAL_STATUS as OWNER_CHROME_FOCUS_GATE
from verification_engine import (
    TEMPORAL_COMPLETE,
    TERMINAL_SUCCESS,
    TERMINAL_UNCERTAIN,
    AssignedCheckResult,
    NextCheckResult,
    classify_after_executor,
    should_stop_on_evidence_gap,
    should_stop_on_prior_fail_blocked,
)


NextCheckFn = Callable[[], NextCheckResult]
AssignedCheckFn = Callable[[dict[str, Any]], AssignedCheckResult]


@dataclass
class VerificationLoopOutcome:
    status: str
    temporal_status: str | None
    change: str
    plan_id: str | None
    progress_pass: int
    progress_total: int
    next_check_runs: int
    executor_runs: int
    message: str
    last_next_check: dict[str, Any] | None = None
    last_assigned_check: dict[str, Any] | None = None
    failed_required_check_id: str | None = None
    blocked_required_check_id: str | None = None
    result_path: str | None = None
    result_status: str | None = None
    target_id: str | None = None
    capability_id: str | None = None
    executor: str | None = None
    next_action: str | None = None
    block_reason: str | None = None
    ledger_path: str | None = None
    session_dependency: str = "NONE"
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "status": self.status,
            "temporal_status": self.temporal_status,
            "change": self.change,
            "plan_id": self.plan_id,
            "progress_pass": self.progress_pass,
            "progress_total": self.progress_total,
            "progress": f"{self.progress_pass}/{self.progress_total}",
            "next_check_runs": self.next_check_runs,
            "executor_runs": self.executor_runs,
            "message": self.message,
            "last_next_check": self.last_next_check,
            "last_assigned_check": self.last_assigned_check,
            "failed_required_check_id": self.failed_required_check_id,
            "blocked_required_check_id": self.blocked_required_check_id,
            "result_path": self.result_path,
            "result_status": self.result_status,
            "target_id": self.target_id,
            "capability_id": self.capability_id,
            "executor": self.executor,
            "next_action": self.next_action,
            "block_reason": self.block_reason,
            "ledger_path": self.ledger_path,
            "VERIFICATION_SESSION_DEPENDENCY": self.session_dependency,
        }
        payload.update(self.extras)
        return payload


class VerificationExecutionLoop:
    """Control-flow twin of VerificationExecutionWorkflow (no Temporal)."""

    def __init__(
        self,
        *,
        change: str,
        run_next_check: NextCheckFn,
        run_assigned_check: AssignedCheckFn,
        repo: str | None = None,
        max_executor_runs: int = 64,
        next_check_technical_retries: int = 2,
    ) -> None:
        self.change = change
        self.repo = repo
        self.run_next_check = run_next_check
        self.run_assigned_check = run_assigned_check
        self.max_executor_runs = max_executor_runs
        self.next_check_technical_retries = next_check_technical_retries

    def _invoke_next_check(self) -> NextCheckResult:
        attempts = 0
        while True:
            result = self.run_next_check()
            if result.completed or result.status != "NEXT_CHECK_TECHNICAL_FAILURE":
                return result
            attempts += 1
            if attempts > self.next_check_technical_retries:
                return result

    def run(self) -> VerificationLoopOutcome:
        next_check_runs = 0
        executor_runs = 0
        last_next: NextCheckResult | None = None
        last_assigned: AssignedCheckResult | None = None

        while True:
            next_check = self._invoke_next_check()
            next_check_runs += 1
            last_next = next_check

            if not next_check.completed:
                return VerificationLoopOutcome(
                    status="NEXT_CHECK_TECHNICAL_FAILURE",
                    temporal_status=None,
                    change=self.change,
                    plan_id=next_check.plan_id,
                    progress_pass=next_check.progress_pass,
                    progress_total=next_check.progress_total,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    message="next-check failed after technical retries",
                    last_next_check=next_check.to_dict(),
                    last_assigned_check=last_assigned.to_dict() if last_assigned else None,
                    block_reason=next_check.block_reason,
                    ledger_path=next_check.ledger_path,
                )

            terminal = classify_after_executor(next_check=next_check)
            # Before any executor: success / block statuses from next-check alone.
            if next_check.status == TERMINAL_SUCCESS:
                return VerificationLoopOutcome(
                    status=TERMINAL_SUCCESS,
                    temporal_status=TEMPORAL_COMPLETE,
                    change=self.change,
                    plan_id=next_check.plan_id,
                    progress_pass=next_check.progress_pass,
                    progress_total=next_check.progress_total,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    message="All frozen required checks have valid PASS evidence",
                    last_next_check=next_check.to_dict(),
                    last_assigned_check=last_assigned.to_dict() if last_assigned else None,
                    next_action=f"/opsx-verify change={self.change} mode=archive",
                    ledger_path=next_check.ledger_path,
                )

            if terminal is not None and next_check.status != "CHECK_REQUIRED":
                return self._from_terminal(
                    terminal,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    last_next=next_check,
                    last_assigned=last_assigned,
                )

            # CHECK_REQUIRED with last_result FAIL/BLOCKED / evidence issues
            # after a prior executor (or resume): stop without re-running unless
            # Owner gave a newer smoke ack after a pre-GO BLOCKED result.
            if terminal is not None and should_stop_on_prior_fail_blocked(
                repo=self.repo,
                next_check=next_check,
            ):
                return self._from_terminal(
                    terminal,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    last_next=next_check,
                    last_assigned=last_assigned,
                )
            last_executed_id = (
                last_assigned.required_check_id if last_assigned else None
            )
            if terminal is not None and should_stop_on_evidence_gap(
                next_check, last_executed_id
            ):
                return self._from_terminal(
                    terminal,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    last_next=next_check,
                    last_assigned=last_assigned,
                )

            if next_check.status != "CHECK_REQUIRED" or not next_check.execute_assignment:
                return VerificationLoopOutcome(
                    status=next_check.status or "BLOCKED",
                    temporal_status=None,
                    change=self.change,
                    plan_id=next_check.plan_id,
                    progress_pass=next_check.progress_pass,
                    progress_total=next_check.progress_total,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    message="next-check did not yield an executable assignment",
                    last_next_check=next_check.to_dict(),
                    last_assigned_check=last_assigned.to_dict() if last_assigned else None,
                    block_reason=next_check.block_reason,
                    ledger_path=next_check.ledger_path,
                )

            if executor_runs >= self.max_executor_runs:
                return VerificationLoopOutcome(
                    status="MAX_EXECUTOR_RUNS_REACHED",
                    temporal_status=None,
                    change=self.change,
                    plan_id=next_check.plan_id,
                    progress_pass=next_check.progress_pass,
                    progress_total=next_check.progress_total,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    message="Executor run budget exceeded (safety stop)",
                    last_next_check=next_check.to_dict(),
                    last_assigned_check=last_assigned.to_dict() if last_assigned else None,
                )

            assignment = dict(next_check.execute_assignment)
            assigned = self.run_assigned_check(assignment)
            last_assigned = assigned

            if assigned.owner_chrome_focus_gate_pending:
                return VerificationLoopOutcome(
                    status=OWNER_CHROME_FOCUS_GATE,
                    temporal_status=None,
                    change=self.change,
                    plan_id=next_check.plan_id,
                    progress_pass=next_check.progress_pass,
                    progress_total=next_check.progress_total,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    message="Owner must focus game Chrome before smoke_acceptance",
                    last_next_check=next_check.to_dict(),
                    last_assigned_check=assigned.to_dict(),
                    blocked_required_check_id=assignment.get("required_check_id"),
                    capability_id=assignment.get("capability_id"),
                    executor=assignment.get("executor"),
                    next_action=(
                        assigned.owner_chrome_focus_gate or {}
                    ).get("resume_command"),
                    block_reason="owner_chrome_focus_required",
                    ledger_path=next_check.ledger_path,
                    extras={
                        "owner_chrome_focus_gate": assigned.owner_chrome_focus_gate,
                    },
                )

            executor_runs += 1

            if assigned.uncertain and not assigned.durable_result_found:
                return VerificationLoopOutcome(
                    status=TERMINAL_UNCERTAIN,
                    temporal_status=None,
                    change=self.change,
                    plan_id=next_check.plan_id,
                    progress_pass=next_check.progress_pass,
                    progress_total=next_check.progress_total,
                    next_check_runs=next_check_runs,
                    executor_runs=executor_runs,
                    message="Executor completion uncertain and no durable BOUNDED_CHECK_RESULT",
                    last_next_check=next_check.to_dict(),
                    last_assigned_check=assigned.to_dict(),
                    failed_required_check_id=assignment.get("required_check_id"),
                    result_path=assignment.get("expected_result_path"),
                    executor=assignment.get("executor"),
                    next_action="Inspect expected_result_path; do not blind-retry executor",
                )

            # Always reconcile via next-check (even when agent response was lost
            # but durable result exists).
            continue

    def _from_terminal(
        self,
        terminal: dict[str, Any],
        *,
        next_check_runs: int,
        executor_runs: int,
        last_next: NextCheckResult,
        last_assigned: AssignedCheckResult | None,
    ) -> VerificationLoopOutcome:
        status = str(terminal["status"])
        return VerificationLoopOutcome(
            status=status,
            temporal_status=terminal.get("temporal_status"),
            change=self.change,
            plan_id=terminal.get("plan_id") or last_next.plan_id,
            progress_pass=int(terminal.get("progress_pass") or last_next.progress_pass),
            progress_total=int(terminal.get("progress_total") or last_next.progress_total),
            next_check_runs=next_check_runs,
            executor_runs=executor_runs,
            message=status,
            last_next_check=last_next.to_dict(),
            last_assigned_check=last_assigned.to_dict() if last_assigned else None,
            failed_required_check_id=terminal.get("failed_required_check_id"),
            blocked_required_check_id=terminal.get("blocked_required_check_id"),
            result_path=terminal.get("result_path"),
            result_status=terminal.get("result_status"),
            target_id=terminal.get("target_id"),
            capability_id=terminal.get("capability_id"),
            executor=terminal.get("executor"),
            next_action=terminal.get("next_action"),
            block_reason=terminal.get("block_reason"),
            ledger_path=terminal.get("ledger_path") or last_next.ledger_path,
        )
