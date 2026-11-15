"""Детерминированное построение ``findings[]`` из ``quality_gate_diagnostics``."""

from __future__ import annotations

from typing import Any, Mapping

from agents.verify.group_error.repair_workflow import classify_repair_workflow
from agents.verify.group_error.schemas import Finding, GateToolId
from workflows.verify.gate_repair.static_scope import gate_issue_in_product_static_scope

_GateTool = tuple[GateToolId, str, str]


def _gate_lines(diagnostics: Mapping[str, Any]) -> list[_GateTool]:
    """Порядок совпадает с ``QualityGateDiagnostics.total_count``."""
    return [
        ("pytest", "quality_gate_diagnostics.pytest", "TEST_FAILURE"),
        ("pyright", "quality_gate_diagnostics.pyright", "TYPE_ERROR"),
        ("mypy", "quality_gate_diagnostics.mypy", "TYPE_ERROR"),
    ]


def _category_for_line(*, tool: str, line: str, default: str) -> str:
    """Грубая категоризация по тексту issue (для handoff, не для gate verdict)."""
    lower = line.lower()
    if tool == "pytest":
        return "TEST_FAILURE"
    if "import-untyped" in lower or "import-not-found" in lower:
        return "STATIC_ANALYSIS"
    if "reportunused" in lower or "lint" in lower:
        return "LINT_ERROR"
    if tool == "pyright":
        return "TYPE_ERROR"
    if "name-defined" in lower or "attr-defined" in lower:
        return "CONTRACT_VIOLATION"
    return default


def build_findings_from_diagnostics(
    diagnostics: Mapping[str, Any] | None,
) -> list[Finding]:
    """Собрать один finding на каждую строку в diagnostics (machine source of truth)."""
    if not diagnostics:
        return []
    findings: list[Finding] = []
    seq = 0
    for tool, evidence_ref, default_category in _gate_lines(diagnostics):
        raw = diagnostics.get(tool)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, str):
                continue
            observation = item.strip()
            if not observation:
                continue
            if tool in ("pyright", "mypy") and not gate_issue_in_product_static_scope(observation):
                continue
            seq += 1
            findings.append(
                Finding(
                    finding_id=f"F-{seq:04d}",
                    source_tool=tool,
                    repair_workflow=classify_repair_workflow(
                        source_tool=tool,
                        observation=observation,
                    ),
                    category=_category_for_line(
                        tool=tool,
                        line=observation,
                        default=default_category,
                    ),
                    observation=observation,
                    classification="FACT",
                    evidence_refs=[evidence_ref],
                ),
            )
    return findings
