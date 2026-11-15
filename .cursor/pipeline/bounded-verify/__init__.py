# -*- coding: utf-8 -*-
"""Bounded verification plan + ledger + executor authorities (R-V1-005A/B/C).

Публичный API: plan freeze (SLICE-001), ledger schedule_write (SLICE-002),
CURRENT-only executor (SLICE-003).
"""

from bounded_verify_executor import (
    ASSIGNMENT_MISMATCH,
    ASSIGNMENT_NOT_CURRENT,
    BoundedVerifyExecutorError,
    NO_CURRENT_ASSIGNMENT,
    RunCurrentCheckResult,
    run_current_check,
)
from bounded_verify_ledger import (
    BoundedVerifyLedgerError,
    ScheduleWriteResult,
    ledger_carrier_path,
    read_ledger,
    schedule_write,
    scheduler_lock_path,
)
from bounded_verify_compose import compose_and_draft_plan, compose_required_checks
from bounded_verify_plan import (
    BoundedVerifyPlanError,
    FrozenPlan,
    PlanDraft,
    assert_plan_mutable,
    compute_plan_fingerprint,
    draft_plan,
    freeze_plan,
    plan_carrier_path,
    read_plan,
)
from bounded_verify_result import (
    BoundedVerifyResultError,
    DrainDecision,
    ExecutionOutcome,
    ExpectedAssignment,
    ResultRecord,
    create_result,
    validate_result_for_drain,
)

__all__ = [
    "ASSIGNMENT_MISMATCH",
    "ASSIGNMENT_NOT_CURRENT",
    "BoundedVerifyExecutorError",
    "BoundedVerifyLedgerError",
    "BoundedVerifyPlanError",
    "BoundedVerifyResultError",
    "DrainDecision",
    "ExecutionOutcome",
    "ExpectedAssignment",
    "FrozenPlan",
    "NO_CURRENT_ASSIGNMENT",
    "PlanDraft",
    "ResultRecord",
    "RunCurrentCheckResult",
    "ScheduleWriteResult",
    "assert_plan_mutable",
    "compose_and_draft_plan",
    "compose_required_checks",
    "compute_plan_fingerprint",
    "create_result",
    "draft_plan",
    "freeze_plan",
    "ledger_carrier_path",
    "plan_carrier_path",
    "read_ledger",
    "read_plan",
    "run_current_check",
    "schedule_write",
    "scheduler_lock_path",
    "validate_result_for_drain",
]
