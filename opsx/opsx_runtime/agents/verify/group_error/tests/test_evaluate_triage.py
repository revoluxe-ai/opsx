"""Tests for evaluate_triage_result and scenario fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from agents.verify.group_error.evaluate_triage import evaluate_triage_result
from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import GroupErrorResult

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_legacy(name: str) -> GroupErrorResult:
    raw = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    return normalize_group_error_result(GroupErrorResult.model_validate(raw))


def test_evaluate_triage_result_empty_violations_scenario_a():
    diagnostics = {
        "pytest": [],
        "pyright": ["src/stroy/x.py:1: error: missing stub"],
        "mypy": ["src/stroy/x.py:1: error: Cannot find implementation"],
    }
    raw = json.loads((_FIXTURES / "scenario_a_cross_tool.json").read_text(encoding="utf-8"))
    result = GroupErrorResult.model_validate(raw)
    violations = evaluate_triage_result(diagnostics, result)
    assert violations == []


def test_evaluate_triage_result_detects_bad_coverage():
    diagnostics = {"pytest": ["fail"], "pyright": [], "mypy": []}
    raw = json.loads((_FIXTURES / "scenario_a_cross_tool.json").read_text(encoding="utf-8"))
    result = GroupErrorResult.model_validate(raw)
    violations = evaluate_triage_result(diagnostics, result)
    assert any("count" in v or "missing" in v or "coverage" in v for v in violations)


def test_scenario_b_mass_pytest_becomes_singleton_rows():
    result = _load_legacy("scenario_b_mass_pytest.json")
    assert len(result.grouped_errors) == 3
    assert all(len(row.finding_ids) == 1 for row in result.grouped_errors)
    assert all(f.source_tool == "pytest" for f in result.findings)


def test_scenario_c_same_file_two_rows():
    result = _load_legacy("scenario_c_same_file_split.json")
    assert len(result.grouped_errors) == 2
    assert {row.finding_ids[0] for row in result.grouped_errors} == {"F-0001", "F-0002"}


def test_scenario_d_similar_text_two_rows():
    result = _load_legacy("scenario_d_similar_text_split.json")
    assert len(result.grouped_errors) == 2


def test_scenario_e_pytest_pyright_cross_tool():
    result = _load_legacy("scenario_e_pytest_pyright.json")
    assert len(result.grouped_errors) == 1
    row = result.grouped_errors[0]
    assert set(row.instruments) == {"pytest", "pyright"}
    assert len(row.finding_ids) == 2
    diagnostics = {
        "pytest": ["FAILED tests/test_api.py::test_handler - TypeError: bad sig"],
        "pyright": ["src/stroy/handler.py:12: error: Argument type mismatch for handle"],
        "mypy": [],
    }
    raw = json.loads((_FIXTURES / "scenario_e_pytest_pyright.json").read_text(encoding="utf-8"))
    assert evaluate_triage_result(diagnostics, GroupErrorResult.model_validate(raw)) == []


def test_scenario_f_full_coverage_partition():
    result = _load_legacy("scenario_f_full_coverage.json")
    all_ids = [fid for row in result.grouped_errors for fid in row.finding_ids]
    assert len(all_ids) == len(result.findings)
    assert len(set(all_ids)) == len(result.findings)
