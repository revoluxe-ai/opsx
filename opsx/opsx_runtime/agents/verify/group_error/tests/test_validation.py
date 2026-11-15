"""Tests for triage validation invariants."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    FindingGroup,
    GroupedError,
    InputIntegrity,
    GroupErrorResult,
    TriageSummary,
)
from agents.verify.group_error.validation import (
    recompute_triage_summary,
    validate_cross_tool_groups,
    validate_finding_coverage,
    validate_group_diagnostic_sources,
    validate_grouped_errors,
)


def _cross_tool_group(**updates: object) -> FindingGroup:
    base = FindingGroup(
        group_id="TG-001",
        matched_problem="symbol foo type mismatch",
        match_basis="pyright and mypy on foo at pkg/a.py:10",
        finding_ids=["F-0001", "F-0002"],
        diagnostic_sources=["pytest", "mypy"],
    )
    if updates:
        return base.model_copy(update=updates)
    return base


def _norm(**updates: object) -> GroupErrorResult:
    body = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1"),
        input_integrity=InputIntegrity(status="COMPLETE"),
        findings=[
            Finding(finding_id="F-0001", source_tool="pytest", observation="a"),
            Finding(finding_id="F-0002", source_tool="mypy", observation="b"),
        ],
        ungrouped_finding_ids=["F-0001", "F-0002"],
        triage_summary=TriageSummary(findings_total=2, ungrouped_total=2),
    )
    if updates:
        body = body.model_copy(update=updates)
    return normalize_group_error_result(body)


def test_validate_finding_coverage_accepts_full_partition():
    validate_finding_coverage(_norm())


def test_validate_finding_coverage_missing_id():
    result = _norm()
    result = result.model_copy(
        update={
            "grouped_errors": [
                GroupedError(
                    error_id="F-0001",
                    problem="a",
                    instruments=["pytest"],
                    finding_ids=["F-0001"],
                ),
            ],
        },
    )
    with pytest.raises(ValueError, match="missing"):
        validate_finding_coverage(result)


def test_validate_finding_coverage_duplicate_id():
    result = _norm(
        finding_groups=[_cross_tool_group(finding_ids=["F-0001"])],
        ungrouped_finding_ids=["F-0001", "F-0002"],
    )
    # normalize merges group + ungrouped with overlap — force duplicate
    row = result.grouped_errors[0]
    dup = result.model_copy(
        update={
            "grouped_errors": [
                row,
                row.model_copy(update={"error_id": "E-dup"}),
            ],
        },
    )
    with pytest.raises(ValueError, match="more than once"):
        validate_finding_coverage(dup)


def test_group_error_result_model_rejects_duplicate_partition():
    """ValidationError на hop при дублировании finding_id в grouped_errors."""
    base = _norm(
        finding_groups=[_cross_tool_group()],
        ungrouped_finding_ids=[],
    )
    row = base.grouped_errors[0]
    payload = base.model_dump(mode="python")
    payload["grouped_errors"] = [
        row.model_dump(mode="python"),
        row.model_copy(update={"error_id": "E-dup"}).model_dump(mode="python"),
    ]
    with pytest.raises(ValidationError, match="more than once"):
        GroupErrorResult.model_validate(payload)


def test_validate_group_diagnostic_sources_ok():
    result = _norm(
        finding_groups=[_cross_tool_group()],
        ungrouped_finding_ids=[],
    )
    validate_group_diagnostic_sources(result)


def test_validate_group_diagnostic_sources_mismatch():
    base = _norm(
        finding_groups=[_cross_tool_group()],
        ungrouped_finding_ids=[],
    )
    row = base.grouped_errors[0]
    result = base.model_copy(
        update={
            "grouped_errors": [
                row.model_copy(update={"instruments": ["pytest"]}),
            ],
        },
    )
    with pytest.raises(ValueError, match="instruments"):
        validate_group_diagnostic_sources(result)


def test_validate_grouped_errors_accepts_mypy_only_cluster():
    """Регрессия: одна логическая ошибка, несколько mypy findings — допустимо."""
    result = _norm(
        findings=[
            Finding(finding_id="F-0001", source_tool="mypy", observation="a"),
            Finding(finding_id="F-0002", source_tool="mypy", observation="b"),
        ],
        finding_groups=[
            FindingGroup(
                group_id="G-0001",
                matched_problem="logging_setup cluster",
                match_basis="same file/symbol",
                finding_ids=["F-0001", "F-0002"],
                diagnostic_sources=["mypy"],
            ),
        ],
        ungrouped_finding_ids=[],
        triage_summary=TriageSummary(findings_total=2, ungrouped_total=0),
    )
    validate_grouped_errors(result)
    validate_cross_tool_groups(result)
    assert result.grouped_errors[0].instruments == ["mypy"]


def test_validate_grouped_errors_rejects_empty_findings_row():
    result = _norm(
        findings=[Finding(finding_id="F-0001", source_tool="mypy", observation="a")],
        grouped_errors=[
            GroupedError(
                error_id="E-empty",
                problem="x",
                instruments=["mypy"],
                finding_ids=[],
            ),
        ],
        ungrouped_finding_ids=[],
    )
    with pytest.raises(ValueError, match="requires at least 1 finding"):
        validate_grouped_errors(result)


def test_recompute_triage_summary_counts():
    result = _norm(
        finding_groups=[_cross_tool_group(finding_ids=["F-0001", "F-0002"])],
        ungrouped_finding_ids=[],
    )
    updated = recompute_triage_summary(result)
    assert updated.triage_summary.findings_total == 2
    assert updated.triage_summary.finding_groups_total == 1
    assert updated.triage_summary.ungrouped_total == 0
