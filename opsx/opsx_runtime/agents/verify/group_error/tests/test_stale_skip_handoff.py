"""Регрессия: снятие устаревшего diagnostic PAUSE после reconcile integrity."""

from __future__ import annotations

from agents.verify.diagnostic.schemas import DiagnosticResult
from agents.verify.diagnostic.tests.test_agent import _analysis_state, _repair_order_state
from agents.verify.group_error.stale_skip_handoff import (
    refresh_diagnostic_after_reconciled_skip,
    stale_group_error_skip_should_pause,
)


def _reconciled_complete_state() -> dict[str, object]:
    node = "tests/unit/foo.py::test_bar"
    return {
        "verify_dispatch_mode": "per_finding",
        "group_error_execution_status": "skipped_input_integrity",
        "quality_gate_diagnostics": {
            "pytest": [f"pytest: {node} - PYTEST_SUITE_TIMEOUT"],
            "input_integrity_status": "PARTIAL",
            "input_integrity_issues": [],
            "quality_gate_raw": {"pytest": {"exit_code": 1}},
        },
        "analysis_result": _analysis_state(),
        "repair_order_result": _repair_order_state(),
    }


def test_stale_skip_pause_suppressed_when_handoff_complete() -> None:
    state = _reconciled_complete_state()
    assert stale_group_error_skip_should_pause(state) is False


def test_refresh_diagnostic_no_pending_after_resolve() -> None:
    from agents.verify.repair_order.schemas import RepairOrderResult

    state = _reconciled_complete_state()
    diagnostic = DiagnosticResult.model_validate(
        {
            "status": "PAUSE",
            "pause_reason": (
                "group_error hop skipped due to incomplete or invalid diagnostics input; "
                "diagnostic requires input_integrity COMPLETE"
            ),
            "source_identity": {"change_id": "c1"},
            "anchor_unit_id": "",
            "repair_unit": None,
            "remaining_queue": [],
        },
    )
    repair_order = RepairOrderResult.model_validate(state["repair_order_result"])
    refreshed, patch = refresh_diagnostic_after_reconciled_skip(
        state,
        diagnostic,
        repair_order,
        resolved={"F-0001", "F-0002", "F-0003"},
    )
    assert refreshed is None
    assert patch == {}
