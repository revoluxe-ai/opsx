"""Shared deterministic transition policy for verification execution loops.

Used by ``verification_loop.VerificationExecutionLoop`` and
``workflow.VerificationExecutionWorkflow`` so in-process and Temporal paths
converge identically. Scheduler → executor → reconcile; repair is out of scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from recovery_contract import recurring_failure
from verification_engine import (
    TERMINAL_BLOCKED_CHECK,
    TERMINAL_UNCERTAIN,
    NextCheckResult,
    classify_after_executor,
    should_stop_on_evidence_gap,
    should_stop_on_prior_fail_blocked,
    workflow_stop_on_prior_fail_blocked,
)

TERMINAL_NON_CONVERGENT = "VERIFICATION_NON_CONVERGENT_PAUSE"
TERMINAL_BLOCKED_PAUSE = "VERIFICATION_CHECK_BLOCKED_PAUSE"
NEXT_CHECK_TECHNICAL_FAILURE = "NEXT_CHECK_TECHNICAL_FAILURE"

_REASON_RE = re.compile(r"^REASON:\s*(.+)$", re.MULTILINE)
_BLOCKED_STATUS_RE = re.compile(
    r"STATUS:\s*VERIFICATION_CHECK_BLOCKED|RUNTIME_PREFLIGHT_",
    re.MULTILINE,
)

DEFAULT_MAX_EXECUTOR_RUNS = 64
DEFAULT_NEXT_CHECK_TECHNICAL_RETRIES = 1


@dataclass
class TransitionTracker:
    """Causal progress and recurring-failure memory across scheduler cycles."""

    last_progress_key: str | None = None
    last_post_executor_signature: str | None = None
    failure_causes: tuple[str, ...] = field(default_factory=tuple)

    def record_failure_cause(self, cause: str | None) -> None:
        token = (cause or "").strip()
        if not token:
            return
        if token not in self.failure_causes:
            self.failure_causes = self.failure_causes + (token,)


def _as_next_check(nc: NextCheckResult | Mapping[str, Any]) -> NextCheckResult:
    if isinstance(nc, NextCheckResult):
        return nc
    return NextCheckResult(
        completed=bool(nc.get("completed")),
        status=str(nc.get("status") or ""),
        progress_pass=int(nc.get("progress_pass") or 0),
        progress_total=int(nc.get("progress_total") or 0),
        current_required_check_id=nc.get("current_required_check_id"),
        assignment_epoch=nc.get("assignment_epoch"),
        execute_assignment=nc.get("execute_assignment"),
        current_last_result=nc.get("current_last_result"),
        block_reason=nc.get("block_reason"),
        plan_id=nc.get("plan_id"),
        ledger_path=nc.get("ledger_path"),
        raw_output=str(nc.get("raw_output") or ""),
        evidence_missing=bool(nc.get("evidence_missing")),
        evidence_invalid=bool(nc.get("evidence_invalid")),
        rerun_blocked_smoke_ack=bool(nc.get("rerun_blocked_smoke_ack")),
    )


def _assigned_mapping(assigned: Mapping[str, Any]) -> dict[str, Any]:
    return dict(assigned)


def causal_progress_key(nc: NextCheckResult) -> str:
    """Ledger-bound progress predicate (not raw executor counters)."""
    assignment = nc.execute_assignment or {}
    return "|".join(
        [
            str(nc.current_required_check_id or ""),
            str(assignment.get("ledger_revision") or ""),
            str(nc.assignment_epoch or assignment.get("assignment_epoch") or ""),
            str(nc.progress_pass),
            str(nc.progress_total),
            str(nc.plan_id or ""),
        ]
    )


def post_executor_signature(nc: NextCheckResult, assigned: Mapping[str, Any]) -> str:
    """Fingerprint of reconcile-visible state after one executor hop."""
    return "|".join(
        [
            causal_progress_key(nc),
            str(assigned.get("completed")),
            str(assigned.get("uncertain")),
            str(assigned.get("durable_result_found")),
            str(assigned.get("durable_result_status")),
            str(assigned.get("required_check_id")),
        ]
    )


def extract_executor_failure_cause(assigned: Mapping[str, Any]) -> str | None:
    raw = str(assigned.get("raw_output") or "")
    match = _REASON_RE.search(raw)
    if match:
        return match.group(1).strip()
    if _BLOCKED_STATUS_RE.search(raw):
        return "VERIFICATION_CHECK_BLOCKED"
    if assigned.get("started") is False and not assigned.get("durable_result_found"):
        return "executor_not_started"
    return None


def is_deterministic_executor_blocked(assigned: Mapping[str, Any]) -> bool:
    """True when executor failed closed without uncertainty (must PAUSE, not spin)."""
    if assigned.get("durable_result_found"):
        return False
    if assigned.get("uncertain"):
        return False
    raw = str(assigned.get("raw_output") or "")
    if _BLOCKED_STATUS_RE.search(raw):
        return True
    if assigned.get("started") is False and not assigned.get("completed"):
        return True
    if not assigned.get("completed") and not assigned.get("uncertain"):
        return True
    return False


def terminal_from_classifier(
    terminal: dict[str, Any],
    *,
    next_check_runs: int,
    executor_runs: int,
    last_next: Mapping[str, Any],
    last_assigned: Mapping[str, Any] | None,
) -> dict[str, Any]:
    status = str(terminal["status"])
    progress_pass = int(terminal.get("progress_pass") or last_next.get("progress_pass") or 0)
    progress_total = int(
        terminal.get("progress_total") or last_next.get("progress_total") or 0
    )
    return {
        "status": status,
        "temporal_status": terminal.get("temporal_status"),
        "plan_id": terminal.get("plan_id") or last_next.get("plan_id"),
        "progress_pass": progress_pass,
        "progress_total": progress_total,
        "message": status,
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
        "next_check_runs": next_check_runs,
        "executor_runs": executor_runs,
        "last_next_check": dict(last_next),
        "last_assigned_check": dict(last_assigned) if last_assigned else None,
    }


def pause_terminal(
    *,
    status: str,
    message: str,
    nc: NextCheckResult,
    assigned: Mapping[str, Any] | None,
    next_check_runs: int,
    executor_runs: int,
    last_next: Mapping[str, Any],
    block_reason: str | None = None,
) -> dict[str, Any]:
    assignment = nc.execute_assignment or {}
    return {
        "status": status,
        "temporal_status": None,
        "plan_id": nc.plan_id,
        "progress_pass": nc.progress_pass,
        "progress_total": nc.progress_total,
        "message": message,
        "block_reason": block_reason or status,
        "failed_required_check_id": (
            assignment.get("required_check_id") if assigned is None else assigned.get("required_check_id")
        ),
        "blocked_required_check_id": assignment.get("required_check_id"),
        "result_path": assignment.get("expected_result_path"),
        "executor": assignment.get("executor"),
        "next_action": "Owner/process resolves blocker; do not blind-retry executor",
        "ledger_path": nc.ledger_path,
        "next_check_runs": next_check_runs,
        "executor_runs": executor_runs,
        "last_next_check": dict(last_next),
        "last_assigned_check": dict(assigned) if assigned else None,
    }


def evaluate_before_executor(
    tracker: TransitionTracker,
    nc: NextCheckResult | Mapping[str, Any],
    *,
    executor_runs: int,
    next_check_runs: int,
    last_next: Mapping[str, Any],
    last_assigned: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Refuse re-dispatching executor when causal ledger progress did not advance."""
    parsed = _as_next_check(nc)
    if executor_runs <= 0 or parsed.status != "CHECK_REQUIRED":
        return None
    key = causal_progress_key(parsed)
    if tracker.last_progress_key is not None and tracker.last_progress_key == key:
        return pause_terminal(
            status=TERMINAL_NON_CONVERGENT,
            message=(
                "Scheduler re-selected the same assignment without causal ledger "
                "progress since the last executor hop."
            ),
            nc=parsed,
            assigned=last_assigned,
            next_check_runs=next_check_runs,
            executor_runs=executor_runs,
            last_next=last_next,
            block_reason="no_causal_progress",
        )
    return None


def evaluate_after_next_check(
    nc: NextCheckResult | Mapping[str, Any],
    *,
    repo: str | None,
    last_executed_id: str | None,
    next_check_runs: int,
    executor_runs: int,
    last_next: Mapping[str, Any],
    last_assigned: Mapping[str, Any] | None,
    workflow_sandbox: bool = False,
) -> dict[str, Any] | None:
    """Return terminal dict to stop, or None to proceed toward executor/continue."""
    parsed = _as_next_check(nc)
    terminal = classify_after_executor(next_check=parsed)
    if terminal is not None and parsed.status != "CHECK_REQUIRED":
        return terminal_from_classifier(
            terminal,
            next_check_runs=next_check_runs,
            executor_runs=executor_runs,
            last_next=last_next,
            last_assigned=last_assigned,
        )
    if terminal is not None:
        if workflow_sandbox:
            stop_prior = workflow_stop_on_prior_fail_blocked(parsed)
        else:
            stop_prior = should_stop_on_prior_fail_blocked(repo=repo, next_check=parsed)
        if stop_prior:
            return terminal_from_classifier(
                terminal,
                next_check_runs=next_check_runs,
                executor_runs=executor_runs,
                last_next=last_next,
                last_assigned=last_assigned,
            )
        if should_stop_on_evidence_gap(parsed, last_executed_id):
            return terminal_from_classifier(
                terminal,
                next_check_runs=next_check_runs,
                executor_runs=executor_runs,
                last_next=last_next,
                last_assigned=last_assigned,
            )
    return None


def evaluate_after_assigned_check(
    tracker: TransitionTracker,
    nc: NextCheckResult | Mapping[str, Any],
    assigned: Mapping[str, Any],
    *,
    next_check_runs: int,
    executor_runs: int,
    last_next: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Post-executor policy: BLOCKED → PAUSE, recurring cause → NON_CONVERGENT."""
    parsed = _as_next_check(nc)
    mapped = _assigned_mapping(assigned)

    cause = extract_executor_failure_cause(mapped)
    recurring = recurring_failure(tracker.failure_causes, cause or "")
    if recurring is not None:
        tracker.record_failure_cause(cause)
        return pause_terminal(
            status=TERMINAL_NON_CONVERGENT,
            message=recurring.message,
            nc=parsed,
            assigned=mapped,
            next_check_runs=next_check_runs,
            executor_runs=executor_runs,
            last_next=last_next,
            block_reason=recurring.status,
        )

    if is_deterministic_executor_blocked(mapped):
        tracker.record_failure_cause(cause or TERMINAL_BLOCKED_PAUSE)
        return pause_terminal(
            status=TERMINAL_BLOCKED_PAUSE,
            message=(
                "Executor returned deterministic BLOCKED without durable "
                "identity-bound evidence; pausing instead of re-selecting check."
            ),
            nc=parsed,
            assigned=mapped,
            next_check_runs=next_check_runs,
            executor_runs=executor_runs,
            last_next=last_next,
            block_reason=cause or "executor_blocked",
        )

    if mapped.get("uncertain") and not mapped.get("durable_result_found"):
        return {
            "status": TERMINAL_UNCERTAIN,
            "temporal_status": None,
            "plan_id": parsed.plan_id,
            "progress_pass": parsed.progress_pass,
            "progress_total": parsed.progress_total,
            "message": (
                "Executor completion uncertain and no durable BOUNDED_CHECK_RESULT"
            ),
            "failed_required_check_id": mapped.get("required_check_id"),
            "result_path": mapped.get("expected_result_path"),
            "executor": mapped.get("executor"),
            "next_action": "Inspect expected_result_path; reconcile durable evidence first",
            "ledger_path": parsed.ledger_path,
            "next_check_runs": next_check_runs,
            "executor_runs": executor_runs,
            "last_next_check": dict(last_next),
            "last_assigned_check": mapped,
        }

    signature = post_executor_signature(parsed, mapped)
    progress_key = causal_progress_key(parsed)
    if (
        tracker.last_post_executor_signature == signature
        and not mapped.get("durable_result_found")
        and parsed.status == "CHECK_REQUIRED"
    ):
        return pause_terminal(
            status=TERMINAL_NON_CONVERGENT,
            message=(
                "Same unresolved causal state after executor; refusing another "
                "scheduler cycle without ledger progress."
            ),
            nc=parsed,
            assigned=mapped,
            next_check_runs=next_check_runs,
            executor_runs=executor_runs,
            last_next=last_next,
            block_reason="no_causal_progress",
        )

    tracker.last_post_executor_signature = signature
    if mapped.get("durable_result_found"):
        tracker.last_progress_key = progress_key
    elif tracker.last_progress_key == progress_key and not mapped.get("durable_result_found"):
        return pause_terminal(
            status=TERMINAL_NON_CONVERGENT,
            message="Ledger progress key unchanged after executor without durable PASS.",
            nc=parsed,
            assigned=mapped,
            next_check_runs=next_check_runs,
            executor_runs=executor_runs,
            last_next=last_next,
            block_reason="no_causal_progress",
        )
    tracker.last_progress_key = progress_key
    if cause:
        tracker.record_failure_cause(cause)
    return None
