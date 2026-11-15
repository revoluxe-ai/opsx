"""Тесты shrink analysis после environment_prep."""

from __future__ import annotations

from agents.verify.environment_prep.queue_shrink import shrink_group_error_for_resolved
from agents.verify.group_error.gate_only_triage import build_gate_only_group_error_result
from agents.state import GraphState


def test_shrink_removes_resolved_findings():
    state: GraphState = {
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
            "pytest": [
                "pytest: fixture 'repo_root' not found",
                "pytest: tests/unit/foo.py::test_bar FAILED",
            ],
        },
    }
    analysis = build_gate_only_group_error_result(
        change_id="c1",
        execution_id=None,
        state=state,
    )
    shrunk = shrink_group_error_for_resolved(analysis, {"F-0001"})
    assert len(shrunk.findings) == 1
    assert shrunk.findings[0].finding_id == "F-0002"
    assert len(shrunk.grouped_errors) == 1
