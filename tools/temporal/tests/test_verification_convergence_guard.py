# -*- coding: utf-8 -*-
"""Regression: verification loop must not re-select checks without causal progress."""

from __future__ import annotations

from pathlib import Path

from verification_engine import NextCheckResult
from verification_loop import VerificationExecutionLoop
from verification_transition_policy import (
    TERMINAL_BLOCKED_PAUSE,
    TERMINAL_NON_CONVERGENT,
)


def _stuck_next_check() -> NextCheckResult:
    assignment = {
        "bounded_verify_target": True,
        "executor": "bounded-executor",
        "change_id": "stuck-change",
        "plan_id": "plan-1",
        "required_check_id": "pyright-strict",
        "check_id": "pyright-strict",
        "assignment_epoch": 1,
        "ledger_revision": 1,
    }
    return NextCheckResult(
        completed=True,
        status="CHECK_REQUIRED",
        progress_pass=0,
        progress_total=2,
        current_required_check_id="pyright-strict",
        assignment_epoch=1,
        execute_assignment=assignment,
        current_last_result=None,
        block_reason=None,
        plan_id="plan-1",
        ledger_path="/tmp/ledger.json",
        raw_output="STATUS: CHECK_REQUIRED",
    )


def test_deterministic_blocked_executor_pauses_instead_of_spinning(tmp_path: Path) -> None:
    """Reproduce CHECK_REQUIRED → executor with no durable progress → PAUSE (not 64 retries)."""
    nc = _stuck_next_check()
    calls = {"next": 0, "exec": 0}

    def run_next_check() -> NextCheckResult:
        calls["next"] += 1
        return nc

    def run_assigned_check(_assignment: dict) -> object:
        from verification_engine import AssignedCheckResult

        calls["exec"] += 1
        return AssignedCheckResult(
            completed=False,
            uncertain=False,
            durable_result_found=False,
            durable_result_status=None,
            expected_result_path=None,
            executor="bounded-executor",
            required_check_id="pyright-strict",
            raw_output=(
                "STATUS: VERIFICATION_CHECK_BLOCKED\n"
                "REASON: RUNTIME_PREFLIGHT_TOOL_UNAVAILABLE\n"
            ),
            started=False,
        )

    outcome = VerificationExecutionLoop(
        change="stuck-change",
        repo=str(tmp_path),
        run_next_check=run_next_check,
        run_assigned_check=run_assigned_check,
        max_executor_runs=64,
    ).run()

    assert outcome.status == TERMINAL_BLOCKED_PAUSE
    assert calls["exec"] == 1
    assert calls["next"] == 1
    assert outcome.executor_runs == 1


def test_repeat_post_executor_signature_is_non_convergent(tmp_path: Path) -> None:
    """Same reconcile signature without durable PASS must not loop until max budget."""
    nc = _stuck_next_check()
    calls = {"exec": 0, "next": 0}

    def run_next_check() -> NextCheckResult:
        calls["next"] += 1
        return nc

    def run_assigned_check(_assignment: dict) -> object:
        from verification_engine import AssignedCheckResult

        calls["exec"] += 1
        return AssignedCheckResult(
            completed=True,
            uncertain=False,
            durable_result_found=False,
            durable_result_status=None,
            expected_result_path=None,
            executor="bounded-executor",
            required_check_id="pyright-strict",
            raw_output="STATUS: PASS\n",
            started=True,
        )

    outcome = VerificationExecutionLoop(
        change="stuck-change",
        repo=str(tmp_path),
        run_next_check=run_next_check,
        run_assigned_check=run_assigned_check,
        max_executor_runs=64,
    ).run()

    assert outcome.status == TERMINAL_NON_CONVERGENT
    assert calls["exec"] == 1
    assert calls["next"] == 2
