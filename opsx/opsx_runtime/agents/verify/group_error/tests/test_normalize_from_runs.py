"""Нормализация triage на фикстурах из реальных прогонов invest-yalta."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.group_error.validation import (
    validate_cross_tool_groups,
    validate_finding_coverage,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "from_runs"


def _load(name: str) -> GroupErrorResult:
    raw = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    return normalize_group_error_result(GroupErrorResult.model_validate(raw))


def test_invest_yalta_mypy_logging_cluster_passes_validation():
    result = _load("invest_yalta_mypy_logging_cluster.json")
    mypy_row = next(r for r in result.grouped_errors if r.error_id == "G-0001")
    assert mypy_row.instruments == ["mypy"]
    assert len(mypy_row.finding_ids) == 5
    validate_finding_coverage(result)
    validate_cross_tool_groups(result)


def test_invest_yalta_phantom_instrument_stripped_to_mypy():
    result = _load("invest_yalta_phantom_instrument.json")
    assert len(result.grouped_errors) == 1
    assert result.grouped_errors[0].instruments == ["mypy"]
    validate_cross_tool_groups(result)


def test_invest_yalta_pytest_mypy_two_instruments():
    result = _load("invest_yalta_pytest_mypy_same_symbol.json")
    assert len(result.grouped_errors) == 1
    assert set(result.grouped_errors[0].instruments) == {"pytest", "mypy"}
    validate_cross_tool_groups(result)


def test_legacy_all_ungrouped_scenario_b_expands_to_rows():
    from pathlib import Path as P

    legacy_path = P(__file__).resolve().parent / "fixtures" / "scenario_b_mass_pytest.json"
    raw = json.loads(legacy_path.read_text(encoding="utf-8"))
    result = normalize_group_error_result(GroupErrorResult.model_validate(raw))
    assert len(result.grouped_errors) == 3
    assert result.ungrouped_finding_ids == []
    validate_finding_coverage(result)
