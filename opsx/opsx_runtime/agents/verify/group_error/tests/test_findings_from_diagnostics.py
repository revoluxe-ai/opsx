"""Tests for deterministic findings expansion."""

from __future__ import annotations

from agents.verify.group_error.findings_from_diagnostics import (
    build_findings_from_diagnostics,
)


def test_build_findings_order_and_ids():
    diagnostics = {
        "pytest": [],
        "pyright": ["src/stroy/a.py:1: oops"],
        "mypy": [
            "src/stroy/b.py:2: err [name-defined]",
            "src/stroy/c.py:3: stub [import-untyped]",
        ],
        "total_count": 3,
    }
    findings = build_findings_from_diagnostics(diagnostics)
    assert len(findings) == 3
    assert findings[0].finding_id == "F-0001"
    assert findings[0].source_tool == "pyright"
    assert findings[0].evidence_refs == ["quality_gate_diagnostics.pyright"]
    assert findings[1].finding_id == "F-0002"
    assert findings[1].category == "CONTRACT_VIOLATION"
    assert findings[2].category == "STATIC_ANALYSIS"
    assert findings[2].repair_workflow == "WF_MYPY_IMPORT"
    assert findings[0].repair_workflow == "WF_PYRIGHT_TYPE"
