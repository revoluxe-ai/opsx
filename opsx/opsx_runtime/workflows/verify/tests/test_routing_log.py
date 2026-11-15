"""Тесты structured routing logs gate-repair (diagnostics в STOP)."""

from __future__ import annotations

import logging

import pytest

from agents.state import GraphState
from workflows.verify.routing_log import (
    log_coverage_activation_node_start,
    log_gate_repair_stop,
    log_quality_gates_pytest_start,
    quality_gate_issue_lines,
)


def test_quality_gate_issue_lines_collects_all_tools() -> None:
    state: GraphState = {
        "quality_gate_diagnostics": {
            "pytest": ["FAILED tests/unit/foo.py::test_bar"],
            "pyright": ["/workspace/src/a.py:1:1 - error: x"],
            "mypy": [],
            "total_count": 2,
        },
    }
    lines = quality_gate_issue_lines(state)
    assert len(lines) == 2
    assert lines[0].startswith("pytest:")
    assert lines[1].startswith("pyright:")


def test_log_coverage_activation_node_start_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state: GraphState = {
        "change_id": "visual",
        "repository_path": r"C:\repo",
    }
    with caplog.at_level(logging.INFO, logger="opsx.verify.routing"):
        log_coverage_activation_node_start(state)
    assert any("coverage_activation node=start change_id=visual" in r.message for r in caplog.records)


def test_log_quality_gates_pytest_start_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state: GraphState = {"change_id": "visual", "quality_gate_timeout_seconds": 3600}
    with caplog.at_level(logging.INFO, logger="opsx.verify.routing"):
        log_quality_gates_pytest_start(
            state,
            scope="FULL_SUITE",
            timeout_s=150.0,
            test_count=10,
            per_test_s=15.0,
        )
    assert any(
        "quality_gates pytest_start change_id=visual scope=FULL_SUITE timeout_s=150"
        in r.message
        for r in caplog.records
    )
    assert any("test_count=10" in r.message for r in caplog.records)


def test_log_gate_repair_stop_emits_remaining_issues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state: GraphState = {
        "quality_gate_verdict": "FAIL",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {
            "pytest": [],
            "pyright": ["/workspace/src/x.py:10:5 - error: bad"],
            "mypy": [],
            "total_count": 1,
        },
        "verify_outer_iteration": 20,
        "verify_loop_status": "VERIFY_LOOP_CAP",
        "verify_workflow_code_changed": False,
    }
    with caplog.at_level(logging.INFO, logger="opsx.verify.routing"):
        log_gate_repair_stop(state, stop_kind="VERIFY_LOOP_CAP", source="test")
    assert any("verify gate_repair STOP" in r.message for r in caplog.records)
    assert any("pyright:" in r.message for r in caplog.records)
    assert any("outer_max=20" in r.message for r in caplog.records)
