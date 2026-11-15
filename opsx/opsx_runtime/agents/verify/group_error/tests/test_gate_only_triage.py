"""Tests for deterministic gate-only group_error triage."""

from __future__ import annotations

import pytest

from agents.state import GraphState
from agents.verify.group_error.gate_only_triage import (
    build_gate_only_group_error_result,
    resolve_group_error_llm_enabled,
    resolve_repair_order_llm_enabled,
)
from agents.verify.group_error.schemas import Finding, InputIntegrity
from agents.verify.group_error.validation import (
    validate_finding_coverage,
    validate_group_error_triage_invariants,
)


def test_build_gate_only_passes_partition_invariants():
    state: GraphState = {
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
            "pytest": ["tests/unit/foo.py::test_bar FAILED"],
            "pyright": ["src/stroy/foo.py:10:5 - error: error two"],
        },
    }
    result = build_gate_only_group_error_result(
        change_id="c1",
        execution_id="run-1",
        state=state,
    )
    assert len(result.findings) == 2
    normalized = result
    validate_finding_coverage(normalized)
    validate_group_error_triage_invariants(normalized)
    assert len(normalized.grouped_errors) == 2
    assert {row.error_id for row in normalized.grouped_errors} == {"F-0001", "F-0002"}


def test_resolve_llm_flags_default_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPSX_GROUP_ERROR_LLM", raising=False)
    monkeypatch.delenv("OPSX_REPAIR_ORDER_LLM", raising=False)
    state: GraphState = {}
    assert resolve_group_error_llm_enabled(state) is False
    assert resolve_repair_order_llm_enabled(state) is False
    assert resolve_group_error_llm_enabled({"group_error_mode": "llm"}) is True


def test_build_gate_only_empty_findings():
    state: GraphState = {
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
        },
    }
    result = build_gate_only_group_error_result(
        change_id="c1",
        execution_id=None,
        state=state,
        input_integrity=InputIntegrity(status="INVALID", issues=["blocked"]),
        identity_status="PARTIAL",
    )
    assert result.findings == []
    assert result.grouped_errors == []
