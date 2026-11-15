"""Тесты CLI-статуса verify_impl при незавершённом pipeline."""

from __future__ import annotations

import pytest

from workflows.implementation.routing import ImplementationLaneTerminalError
from workflows.implementation.routing_contract import RUN_STATUS_PAUSED
from workflows.verify.cli_outcome import (
    VerifyImplIncompleteError,
    VerifyImplProcessExitError,
    format_verify_impl_incomplete_cli_status,
    format_verify_impl_terminal_cli_status,
    raise_if_implementation_lane_terminal,
    raise_if_verify_impl_incomplete,
    raise_unless_security_pass,
    verify_impl_pipeline_complete,
    verify_impl_security_pass,
)


def test_pipeline_complete_requires_quality_gates_or_full_cycle() -> None:
    assert not verify_impl_pipeline_complete(
        {"verdict": "IMPL_OK", "verify_stop_reason": "Coverage Activation"},
    )
    assert not verify_impl_pipeline_complete({"verdict": "IMPL_OK"})
    assert verify_impl_pipeline_complete(
        {
            "verdict": "IMPL_OK",
            "quality_gates_execution_status": "DETERMINISTIC",
        },
    )


def test_paused_lane_raises_for_cli() -> None:
    with pytest.raises(ImplementationLaneTerminalError, match="APPLY_BLOCKED"):
        raise_if_implementation_lane_terminal(
            {
                "implementation_status": RUN_STATUS_PAUSED,
                "implementation_recovery_code": "APPLY_BLOCKED",
                "implementation_stage": "APPLY_DESIGN",
            },
            change_id="visual",
        )


def test_paused_lane_raises_machine_gate_text_when_error_empty() -> None:
    """Пустой ``error`` не скрывает сообщение ``implementation_machine_gate_errors``."""
    message = "SCAFFOLD.json is not a scaffold receipt"
    with pytest.raises(ImplementationLaneTerminalError, match="SCHEMA"):
        raise_if_implementation_lane_terminal(
            {
                "implementation_status": RUN_STATUS_PAUSED,
                "implementation_recovery_code": "APPLY_BLOCKED",
                "implementation_stage": "APPLY_SCAFFOLD",
                "implementation_slice_id": "SLICE-004",
                "error": "",
                "implementation_machine_gate_errors": [
                    {"code": "SCHEMA", "message": message},
                ],
            },
            change_id="visual",
        )
    text = format_verify_impl_incomplete_cli_status(
        {
            "implementation_status": RUN_STATUS_PAUSED,
            "implementation_recovery_code": "APPLY_BLOCKED",
            "implementation_stage": "APPLY_SCAFFOLD",
            "implementation_slice_id": "SLICE-004",
            "implementation_machine_gate_errors": [
                {"code": "SCHEMA", "message": message},
            ],
        },
        change_id="visual",
    )
    assert f"implementation_error=SCHEMA: {message}" in text


def test_incomplete_pipeline_raises_instead_of_quiet_status() -> None:
    state = {
        "verdict": "-",
        "planning_readiness_status": "PAUSED",
        "planning_readiness_result": {
            "status": "BLOCKED",
            "code": "COVERAGE_PLAN_MISSING",
            "detail": "plan.receipt.json",
        },
    }
    with pytest.raises(VerifyImplIncompleteError, match="COVERAGE_PLAN_MISSING"):
        raise_if_verify_impl_incomplete(state, change_id="visual")


def test_incomplete_cli_status_mentions_stop_and_reset() -> None:
    text = format_verify_impl_incomplete_cli_status(
        {
            "verdict": "IMPL_OK",
            "verify_stop_reason": "Coverage Activation",
            "coverage_activation_verdict": "COVERAGE_ACTIVATION_PASS",
            "implementation_stage": "APPLY_SCAFFOLD",
            "implementation_slice_id": "SLICE-001",
        },
        change_id="invest-yalta-max-funnel",
    )
    assert "implementation_procedure=APPLY_SCAFFOLD SLICE-001" in text
    assert "verify_impl incomplete" in text
    assert "Coverage Activation" in text
    assert "invest-yalta-max-funnel" in text
    assert "-r" in text
    assert "IMPL_OK" in text


def _security_pass_state(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "security_review_status": "SECURITY_COMPLETE",
        "review": {
            "status": "SECURITY_COMPLETE",
            "findings": [],
            "confirmed_security_issue_count": 0,
        },
        "check_receipt": {"source": "fresh_audit", "saved": True},
        "verdict": "IMPL_OK",
        "quality_gates_execution_status": "DETERMINISTIC",
        "verify_loop_status": "VERIFY_OK",
    }
    base.update(overrides)
    return base


def test_verify_impl_security_pass_accepts_complete_state() -> None:
    assert verify_impl_security_pass(_security_pass_state())


def test_raise_unless_security_pass_ok() -> None:
    raise_unless_security_pass(_security_pass_state(), change_id="visual")


def test_raise_unless_security_pass_paused_raises() -> None:
    with pytest.raises(ImplementationLaneTerminalError, match="PAUSED"):
        raise_if_implementation_lane_terminal(
            {
                "implementation_status": RUN_STATUS_PAUSED,
                "implementation_recovery_code": "APPLY_BLOCKED",
            },
            change_id="visual",
        )


def test_raise_unless_security_pass_apply_blocked_raises() -> None:
    with pytest.raises(VerifyImplProcessExitError, match="APPLY_BLOCKED"):
        raise_unless_security_pass(
            {
                "apply_scaffold_status": "BLOCKED",
                "verdict": "IMPL_OK",
                "quality_gates_execution_status": "DETERMINISTIC",
            },
            change_id="visual",
        )


def test_raise_unless_security_pass_impl_ok_without_security_raises() -> None:
    with pytest.raises(VerifyImplProcessExitError, match="SECURITY_NOT_RUN"):
        raise_unless_security_pass(
            {
                "verdict": "IMPL_OK",
                "quality_gates_execution_status": "DETERMINISTIC",
                "verify_loop_status": "VERIFY_OK",
            },
            change_id="visual",
        )


def test_terminal_cli_status_omits_impl_plan_report() -> None:
    text = format_verify_impl_terminal_cli_status(
        {
            "verdict": "IMPL_OK",
            "marker": "✅ IMPL_OK",
            "report_text": "## Итоговый вердикт\nIMPL_OK — реализация соответствует плану\n✅ IMPL_OK",
            "verify_loop_status": "PAUSE",
            "quality_gate_verdict": "FAIL",
            "quality_gates_execution_status": "DETERMINISTIC",
            "quality_gate_diagnostics": {
                "pytest": ["AttributeError: UxState has no attribute name"],
                "mypy": ["logging_setup.py:35: Name redact_text already defined"],
            },
        },
        change_id="invest-yalta-max-funnel",
    )
    assert "IMPL_OK" not in text
    assert "Итоговый вердикт" not in text
    assert "verify_loop_status=PAUSE" in text
    assert "quality_gate_verdict=FAIL" in text
    assert "invest-yalta-max-funnel" in text
    assert "AttributeError: UxState has no attribute name" in text
    assert "logging_setup.py:35" in text
