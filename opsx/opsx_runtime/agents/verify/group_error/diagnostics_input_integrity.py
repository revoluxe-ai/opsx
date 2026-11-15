"""Детерминированное чтение ``input_integrity`` из ``quality_gate_diagnostics``."""

from __future__ import annotations

from typing import Any, Mapping

from agents.verify.group_error.schemas import InputIntegrity
from workflows.verify.gate_repair.input_integrity import reconcile_handoff_input_integrity

_VALID = frozenset({"COMPLETE", "PARTIAL", "INVALID"})


def input_integrity_from_gate_diagnostics(
    diagnostics: Mapping[str, Any] | None,
) -> InputIntegrity:
    """Построить ``InputIntegrity`` для triage из handoff gates (не из LLM)."""
    if not diagnostics:
        return InputIntegrity(
            status="INVALID",
            issues=["quality_gate_diagnostics missing"],
        )
    status = diagnostics.get("input_integrity_status")
    issues_raw = diagnostics.get("input_integrity_issues")
    issues: list[str] = []
    if isinstance(issues_raw, list):
        issues = [str(item) for item in issues_raw if str(item).strip()]
    if status not in _VALID:
        return InputIntegrity(
            status="INVALID",
            issues=["input_integrity_status missing or invalid in quality_gate_diagnostics"],
        )
    pytest_exit_nonzero: bool | None = None
    raw = diagnostics.get("quality_gate_raw")
    if isinstance(raw, dict):
        pytest_raw = raw.get("pytest")
        if isinstance(pytest_raw, dict):
            exit_code = pytest_raw.get("exit_code")
            if isinstance(exit_code, int):
                pytest_exit_nonzero = exit_code != 0
    promoted_status, promoted_issues = reconcile_handoff_input_integrity(
        diagnostics,
        pytest_exit_nonzero=pytest_exit_nonzero,
    )
    return InputIntegrity(status=promoted_status, issues=promoted_issues)
