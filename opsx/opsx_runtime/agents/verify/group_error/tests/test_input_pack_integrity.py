"""Tests for verification package mypy integrity fields."""

from __future__ import annotations

from agents.verify.group_error.input_pack import build_verification_package


def test_verification_package_includes_input_integrity_from_diagnostics():
    state = {
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_raw": {
            "pytest": {"command": [], "exit_code": 0, "duration_ms": 1},
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 1, "duration_ms": 1, "stdout_tail": "x"},
        },
        "quality_gate_diagnostics": {
            "pytest": [],
            "pyright": [],
            "mypy": ["a.py:1: err"],
            "input_integrity_status": "PARTIAL",
            "input_integrity_issues": ["mypy mismatch"],
        },
    }
    package = build_verification_package(
        change_id="c",
        repository_path=".",
        state=state,
    )
    assert package["diagnostics"]["input_integrity_status"] == "PARTIAL"
    assert any("3.5k" in item for item in package["limitations"])
