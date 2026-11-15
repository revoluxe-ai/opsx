"""Общие helpers элементов finding для dispatcher и repair unit."""

from __future__ import annotations

import re
from typing import Any

from agents.verify.dispatch_unit.repro_cohort import pytest_module, pytest_node_id
from agents.verify.group_error.schemas import Finding
from workflows.verify.gate_repair.diagnostics import lookup_pytest_stall_evidence
from workflows.verify.gate_repair.pytest_failure_excerpt import lookup_pytest_failure_excerpt

_PATH_LINE_RE = re.compile(
    r"^(?P<path>[^\s:]+(?::[^\s:]+)?\.pyw?|[^\s:]+\.[a-zA-Z0-9]+):(?P<line>\d+)",
)


def parse_path_line(observation: str) -> tuple[str | None, int | None]:
    """Извлечь path и line из типичной строки gate (pyright/mypy/pytest)."""
    text = observation.strip()
    match = _PATH_LINE_RE.match(text)
    if not match:
        return None, None
    path = match.group("path")
    try:
        line = int(match.group("line"))
    except ValueError:
        line = None
    return path, line


def finding_item_from_finding(
    finding: Finding,
    *,
    item_kind: str = "finding",
    quality_gate_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Словарь элемента очереди для одного finding."""
    observation = str(finding.observation or "")
    path, line = parse_path_line(observation)
    if path is None and finding.source_tool == "pytest":
        node = pytest_node_id(observation)
        if node is not None:
            path = pytest_module(node)
    pytest_failure_excerpt: str | None = None
    pytest_stall_evidence: dict[str, object] | None = None
    if finding.source_tool == "pytest":
        pytest_failure_excerpt = lookup_pytest_failure_excerpt(
            quality_gate_diagnostics,
            observation=observation,
        )
        stall_row = lookup_pytest_stall_evidence(
            quality_gate_diagnostics,
            observation=observation,
        )
        if stall_row is not None:
            pytest_stall_evidence = stall_row
    return {
        "item_kind": item_kind,
        "finding_id": finding.finding_id,
        "observation": finding.observation or "",
        "source_tool": finding.source_tool,
        "repair_workflow": finding.repair_workflow,
        "path": path,
        "line": line,
        "pytest_failure_excerpt": pytest_failure_excerpt,
        "pytest_stall_evidence": pytest_stall_evidence,
    }
