"""Детерминированная оценка triage result против diagnostics (для тестов и QA)."""

from __future__ import annotations

from typing import Any, Mapping

from agents.verify.group_error.findings_from_diagnostics import (
    build_findings_from_diagnostics,
)
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.validation import (
    validate_finding_coverage,
    validate_grouped_errors,
    validate_grouped_errors_instruments,
)


def evaluate_triage_result(
    diagnostics: Mapping[str, Any] | None,
    result: GroupErrorResult,
) -> list[str]:
    """Собрать нарушения инвариантов; пустой список — результат согласован с входом."""
    violations: list[str] = []
    expected_findings = build_findings_from_diagnostics(diagnostics)
    if len(expected_findings) != len(result.findings):
        violations.append(
            f"findings count {len(result.findings)} != diagnostics lines "
            f"{len(expected_findings)}",
        )
    exp_ids = [f.finding_id for f in expected_findings]
    got_ids = [f.finding_id for f in result.findings]
    if exp_ids != got_ids:
        violations.append("finding_id order or set differs from diagnostics expansion")

    for idx, (exp, got) in enumerate(zip(expected_findings, result.findings, strict=False)):
        if exp.source_tool != got.source_tool:
            violations.append(
                f"{got.finding_id}: source_tool {got.source_tool!r} != "
                f"expected {exp.source_tool!r} at index {idx}",
            )
        if exp.observation != got.observation:
            violations.append(f"{got.finding_id}: observation text must match diagnostics")

    normalized = normalize_group_error_result(result)
    normalized = normalized.model_copy(update={"findings": list(result.findings)})

    try:
        validate_finding_coverage(normalized)
    except ValueError as exc:
        violations.append(str(exc))

    try:
        validate_grouped_errors_instruments(normalized)
    except ValueError as exc:
        violations.append(str(exc))

    try:
        validate_grouped_errors(normalized)
    except ValueError as exc:
        violations.append(str(exc))

    return violations
