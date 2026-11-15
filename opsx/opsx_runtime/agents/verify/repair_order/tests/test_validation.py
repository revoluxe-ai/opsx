"""Tests for repair order validation invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    FindingGroup,
    InputIntegrity,
    GroupErrorResult,
    TriageSummary,
)
from agents.verify.repair_order.schemas import OrderedUnit, RepairOrderResult, SourceIdentity
from agents.verify.repair_order.enrichment import attach_finding_workflows
from agents.verify.repair_order.sort_units import (
    build_baseline_ordered_units,
    sort_units_by_ordered_ids,
)
from agents.verify.repair_order.validation import (
    validate_no_regroup,
    validate_positions,
    validate_unit_coverage,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _finding(finding_id: str, source_tool: str) -> Finding:
    return Finding(
        finding_id=finding_id,
        source_tool=source_tool,  # type: ignore[arg-type]
        repair_workflow="WF_UNKNOWN_GATE",
    )


def _analysis(**updates: object) -> GroupErrorResult:
    body = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1"),
        input_integrity=InputIntegrity(status="COMPLETE"),
        finding_groups=[
            FindingGroup(
                group_id="TG-001",
                finding_ids=["F-0001", "F-0002"],
                matched_problem="same symbol",
                match_basis="cross-tool on foo",
                diagnostic_sources=["pytest", "mypy"],
            ),
        ],
        ungrouped_finding_ids=["F-0003"],
        findings=[
            _finding("F-0001", "pytest"),
            _finding("F-0002", "mypy"),
            _finding("F-0003", "pyright"),
        ],
        triage_summary=TriageSummary(
            findings_total=3,
            finding_groups_total=1,
            ungrouped_total=1,
        ),
    )
    normalized = normalize_group_error_result(body)
    if updates:
        return normalized.model_copy(update=updates)
    return normalized


def _sorted_result(**updates: object) -> RepairOrderResult:
    body = RepairOrderResult(
        status="SORTED",
        source_identity=SourceIdentity(change_id="c1"),
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="GROUP",
                unit_id="TG-001",
                finding_ids=["F-0001", "F-0002"],
                placement_rationale="input group order",
            ),
            OrderedUnit(
                position=2,
                unit_type="GROUP",
                unit_id="F-0003",
                finding_ids=["F-0003"],
                placement_rationale="singleton row",
            ),
        ],
    )
    if updates:
        return body.model_copy(update=updates)
    return body


def test_validate_unit_coverage_accepts_full_partition():
    validate_unit_coverage(_sorted_result(), _analysis())


def test_sort_units_by_ordered_ids_puts_unknown_ids_last_stable():
    baseline = build_baseline_ordered_units(_analysis())
    reordered = sort_units_by_ordered_ids(
        baseline,
        ["F-0003", "TG-001", "UNKNOWN-999"],
    )
    assert [u.unit_id for u in reordered] == ["F-0003", "TG-001"]
    assert reordered[0].finding_ids == ["F-0003"]
    assert reordered[1].finding_ids == ["F-0001", "F-0002"]


def test_validate_unit_coverage_missing_finding():
    result = _sorted_result(
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="GROUP",
                unit_id="TG-001",
                finding_ids=["F-0001", "F-0002"],
                placement_rationale="x",
            ),
        ],
    )
    with pytest.raises(ValueError, match="missing"):
        validate_unit_coverage(result, _analysis())


def test_validate_no_regroup_rejects_wrong_group_members():
    result = _sorted_result(
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="GROUP",
                unit_id="TG-001",
                finding_ids=["F-0001"],
                placement_rationale="x",
            ),
            OrderedUnit(
                position=2,
                unit_type="GROUP",
                unit_id="F-0003",
                finding_ids=["F-0003"],
                placement_rationale="y",
            ),
        ],
    )
    with pytest.raises(ValueError, match="must match triage"):
        validate_no_regroup(result, _analysis())


def test_validate_no_regroup_rejects_bad_singleton_payload():
    result = _sorted_result(
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="GROUP",
                unit_id="TG-001",
                finding_ids=["F-0001", "F-0002"],
                placement_rationale="x",
            ),
            OrderedUnit(
                position=2,
                unit_type="GROUP",
                unit_id="F-0003",
                finding_ids=["F-0002"],
                placement_rationale="y",
            ),
        ],
    )
    with pytest.raises(ValueError, match="must match triage"):
        validate_no_regroup(result, _analysis())


def test_validate_positions_requires_contiguous_one_based():
    result = _sorted_result(
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="GROUP",
                unit_id="TG-001",
                finding_ids=["F-0001", "F-0002"],
                placement_rationale="x",
            ),
            OrderedUnit(
                position=3,
                unit_type="GROUP",
                unit_id="F-0003",
                finding_ids=["F-0003"],
                placement_rationale="y",
            ),
        ],
    )
    with pytest.raises(ValueError, match="positions must be"):
        validate_positions(result)


def test_validate_pause_skips_ordered_invariants():
    pause = RepairOrderResult(
        status="PAUSE",
        source_identity=SourceIdentity(change_id="c1"),
        pause_reason="test",
        ordered_units=[],
    )
    validate_unit_coverage(pause, _analysis())
    validate_no_regroup(pause, _analysis())
    validate_positions(pause)


def test_fixture_scenario_stable_order_matches_validation():
    raw = json.loads((_FIXTURES / "scenario_stable_order.json").read_text(encoding="utf-8"))
    analysis = normalize_group_error_result(
        GroupErrorResult.model_validate(raw["analysis_result"]),
    )
    result = RepairOrderResult.model_validate(raw["repair_order_result"])
    result = attach_finding_workflows(result, analysis)
    validate_unit_coverage(result, analysis)
    validate_no_regroup(result, analysis)
    validate_positions(result)


def test_validate_no_regroup_rejects_missing_group_unit():
    result = _sorted_result(
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="GROUP",
                unit_id="F-0003",
                finding_ids=["F-0003"],
                placement_rationale="only singleton",
            ),
        ],
    )
    with pytest.raises(ValueError, match="GROUP units must cover"):
        validate_no_regroup(result, _analysis())


def test_validate_unit_coverage_duplicate_across_units():
    result = _sorted_result(
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="GROUP",
                unit_id="TG-001",
                finding_ids=["F-0001", "F-0002"],
                placement_rationale="x",
            ),
            OrderedUnit(
                position=2,
                unit_type="GROUP",
                unit_id="F-0003",
                finding_ids=["F-0003", "F-0001"],
                placement_rationale="dup",
            ),
        ],
    )
    with pytest.raises(ValueError, match="more than once"):
        validate_unit_coverage(result, _analysis())
