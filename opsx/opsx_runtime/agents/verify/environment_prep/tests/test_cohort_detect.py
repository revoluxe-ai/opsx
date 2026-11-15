"""Тесты детерминированного cohort_detect."""

from __future__ import annotations

import pytest

from agents.state import GraphState
from agents.verify.environment_prep.cohort_detect import (
    detect_environment_prep_cohorts,
    should_run_environment_prep,
)
from agents.verify.group_error.gate_only_triage import build_gate_only_group_error_result


@pytest.fixture(autouse=True)
def _clear_skip_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPSX_SKIP_ENVIRONMENT_PREP", raising=False)


def _state_with_pytest(*lines: str) -> GraphState:
    return {
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
            "pytest": list(lines),
            "pyright": [],
            "mypy": [],
        },
    }


def test_detect_hermetic_boundary_cohort():
    state = _state_with_pytest(
        "pytest: bug_lifecycle.hermetic_boundary.HermeticBoundaryError: "
        "production repo cannot be registered",
    )
    cohorts = detect_environment_prep_cohorts(state)
    assert len(cohorts.cohorts) == 1
    assert cohorts.cohorts[0].kind == "hermetic_boundary"
    assert cohorts.cohorts[0].finding_ids == ["F-0001"]


def test_detect_shared_fixtures_cohort():
    state = _state_with_pytest("pytest: fixture 'repo_root' not found")
    cohorts = detect_environment_prep_cohorts(state)
    assert cohorts.cohorts[0].kind == "shared_fixtures"


def test_should_run_on_priority_cohort():
    state = _state_with_pytest("pytest: fixture 'repo_root' not found")
    analysis = build_gate_only_group_error_result(
        change_id="c1",
        execution_id=None,
        state=state,
    )
    state["analysis_result"] = analysis.model_dump(mode="json")
    assert should_run_environment_prep(state) is True


def test_should_not_run_when_skip_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPSX_SKIP_ENVIRONMENT_PREP", "1")
    state = _state_with_pytest("pytest: fixture 'repo_root' not found")
    assert should_run_environment_prep(state) is False
