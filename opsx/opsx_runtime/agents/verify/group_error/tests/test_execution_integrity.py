"""Tests for execution-integrity gate before LLM triage."""

from __future__ import annotations

from agents.verify.group_error.execution_integrity import assess_execution_integrity
from agents.verify.group_error.input_pack import build_verification_package
from workflows.verify.gate_repair.pytest_gate_timeout import format_pytest_timeout_evidence


def _minimal_state(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "change_id": "chg",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_raw": {
            "pytest": {"command": [], "exit_code": 1, "duration_ms": 1},
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 0, "duration_ms": 1},
        },
        "quality_gate_diagnostics": {"pytest": ["fail"], "pyright": [], "mypy": []},
    }
    base.update(overrides)
    return base


def test_assess_execution_integrity_ok():
    state = _minimal_state()
    package = build_verification_package(
        change_id="chg",
        repository_path=".",
        state=state,
    )
    verdict, issues = assess_execution_integrity(state=state, package=package)
    assert verdict == "OK"
    assert issues == []


def test_assess_execution_integrity_missing_gates_status():
    state = _minimal_state(quality_gates_execution_status="")
    package = build_verification_package(
        change_id="chg",
        repository_path=".",
        state=state,
    )
    verdict, issues = assess_execution_integrity(state=state, package=package)
    assert verdict == "BLOCKED"
    assert any("quality_gates_execution_status" in i for i in issues)


def test_assess_execution_integrity_runner_invalid():
    state = _minimal_state(execution_status="INVALID")
    package = build_verification_package(
        change_id="chg",
        repository_path=".",
        state=state,
    )
    verdict, issues = assess_execution_integrity(state=state, package=package)
    assert verdict == "BLOCKED"
    assert any("INVALID" in i for i in issues)


def test_assess_execution_integrity_not_run_check():
    state = _minimal_state(quality_gate_raw={})
    package = build_verification_package(
        change_id="chg",
        repository_path=".",
        state=state,
    )
    verdict, issues = assess_execution_integrity(state=state, package=package)
    assert verdict == "BLOCKED"
    assert package["execution_integrity"]["status"] == "INVALID"


def test_suite_timeout_evidence_does_not_block_execution_integrity() -> None:
    node = "tests/unit/foo.py::test_bar"
    evidence = format_pytest_timeout_evidence(
        scope="tests",
        timeout_s=90.0,
        test_count=100,
        per_test_s=15.0,
        last_node_id=node,
        stdout_tail=f"FAILED {node} - PYTEST_SUITE_TIMEOUT\n",
        stderr_tail="",
    )
    state = _minimal_state(
        quality_gate_raw={
            "pytest": {
                "command": [],
                "exit_code": 1,
                "duration_ms": 90_000,
                "error": evidence,
            },
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 0, "duration_ms": 1},
        },
        quality_gate_diagnostics={
            "pytest": [f"pytest: {node} - PYTEST_SUITE_TIMEOUT"],
            "pyright": [],
            "mypy": [],
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
        },
    )
    package = build_verification_package(
        change_id="chg",
        repository_path=".",
        state=state,
    )
    assert package["execution_integrity"]["status"] == "COMPLETE"
    verdict, issues = assess_execution_integrity(state=state, package=package)
    assert verdict == "OK"
    assert issues == []


def test_assess_execution_integrity_explicit_flag():
    state = _minimal_state(triage_execution_integrity="INCOMPLETE")
    package = build_verification_package(
        change_id="chg",
        repository_path=".",
        state=state,
    )
    verdict, issues = assess_execution_integrity(state=state, package=package)
    assert verdict == "BLOCKED"
    assert any("triage_execution_integrity" in i for i in issues)
