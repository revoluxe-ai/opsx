"""Тесты ``resolve_quality_gate_verdict`` и reconcile пустого FAIL."""

from __future__ import annotations

from workflows.verify.gate_repair.diagnostics import (
    QualityGateDiagnostics,
    resolve_quality_gate_verdict,
)
from workflows.verify.gate_repair.schemas import GateCheckResult
from workflows.verify.outer_loop import is_verify_clean, quality_gate_actionable_fail


def _pytest(exit_code: int = 0) -> GateCheckResult:
    return GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=exit_code,
        duration_ms=1,
        stdout_tail="",
        stderr_tail="",
        error=None,
    )


def test_resolve_pass_when_pyright_exit_nonzero_but_no_scoped_issues():
    diag = QualityGateDiagnostics(
        input_integrity_status="COMPLETE",
        pyright=[],
        mypy=[],
        pytest=[],
    )
    assert resolve_quality_gate_verdict(pytest=_pytest(0), diagnostics=diag) == "PASS"


def test_resolve_fail_when_pytest_nonzero():
    diag = QualityGateDiagnostics(input_integrity_status="COMPLETE")
    assert resolve_quality_gate_verdict(pytest=_pytest(1), diagnostics=diag) == "FAIL"


def test_resolve_fail_when_in_scope_findings():
    diag = QualityGateDiagnostics(
        input_integrity_status="COMPLETE",
        pytest=["pytest: FAILED tests/foo.py::test_x - assert False"],
    )
    assert resolve_quality_gate_verdict(pytest=_pytest(0), diagnostics=diag) == "FAIL"


def test_stale_fail_with_zero_findings_is_verify_clean():
    state = {
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {
            "total_count": 0,
            "input_integrity_status": "COMPLETE",
            "pytest": [],
            "pyright": [],
            "mypy": [],
        },
        "quality_gate_raw": {
            "pytest": {"exit_code": 0},
            "pyright": {"exit_code": 1},
            "mypy": {"exit_code": 0},
        },
    }
    assert quality_gate_actionable_fail(state) is False
    assert is_verify_clean(state) is True
