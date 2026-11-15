"""Оценка execution-integrity входа triage (gates/runner), без LLM."""

from __future__ import annotations

from typing import Any, Literal

from workflows.verify.gate_repair.input_integrity import is_pytest_structured_gate_evidence

IntegrityVerdict = Literal["OK", "BLOCKED"]

_RUNNER_BLOCKERS = frozenset(
    {"INVALID", "INCOMPLETE", "UNKNOWN", "FAILED", "BLOCKED", "ERROR"},
)
_GATE_OK_EXECUTION = frozenset({"DETERMINISTIC"})


def assess_execution_integrity(
    *,
    state: dict[str, Any],
    package: dict[str, Any],
) -> tuple[IntegrityVerdict, list[str]]:
    """Решить, можно ли вызывать LLM triage (findings vs целостность прогона).

    Returns:
        ``("OK", [])`` — нормальный triage по findings.
        ``("BLOCKED", issues)`` — пропуск LLM, handoff только с фактами из diagnostics.
    """
    issues: list[str] = []

    explicit = state.get("triage_execution_integrity")
    if isinstance(explicit, str) and explicit.strip().upper() in _RUNNER_BLOCKERS:
        issues.append(
            f"triage_execution_integrity={explicit.strip().upper()} blocks LLM triage",
        )

    runner_status = str(state.get("execution_status") or "").strip().upper()
    if runner_status in _RUNNER_BLOCKERS:
        issues.append(
            f"verification runner execution_status={runner_status} is not triage input",
        )

    qg_exec = str(state.get("quality_gates_execution_status") or "").strip().upper()
    if not qg_exec:
        issues.append("quality_gates_execution_status missing — gates run incomplete")
    elif qg_exec not in _GATE_OK_EXECUTION:
        issues.append(
            f"quality_gates_execution_status={qg_exec} — expected "
            f"{sorted(_GATE_OK_EXECUTION)} for findings triage",
        )

    check_results = package.get("check_results")
    if isinstance(check_results, list):
        for block in check_results:
            if not isinstance(block, dict):
                continue
            check_id = block.get("check_id") or block.get("name") or "?"
            status = str(block.get("status") or "").upper()
            if status == "NOT_RUN":
                issues.append(f"quality gate check {check_id} was NOT_RUN")
            raw_error = block.get("error")
            if raw_error:
                if check_id == "pytest" and is_pytest_structured_gate_evidence(
                    str(raw_error),
                ):
                    continue
                issues.append(
                    f"quality gate check {check_id} subprocess error: {raw_error}",
                )

    raw = state.get("quality_gate_raw")
    diagnostics = state.get("quality_gate_diagnostics")
    if raw and isinstance(raw, dict) and diagnostics is None:
        issues.append(
            "quality_gate_diagnostics missing while quality_gate_raw present",
        )

    package_integrity = package.get("execution_integrity")
    if isinstance(package_integrity, dict):
        pkg_status = str(package_integrity.get("status") or "").upper()
        if pkg_status == "INVALID":
            for item in package_integrity.get("issues") or []:
                if isinstance(item, str) and item not in issues:
                    issues.append(item)

    if issues:
        return "BLOCKED", issues
    return "OK", []
