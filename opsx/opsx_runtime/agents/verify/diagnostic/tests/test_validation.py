"""Tests for diagnostic validation invariants."""

from __future__ import annotations

import pytest

from agents.verify.diagnostic.schemas import (
    DiagnosticResult,
    DiagnosticSourceIdentity,
    MergedUnitLinkage,
    RepairUnit,
    RemainingQueueUnit,
)
from agents.verify.diagnostic.validation import (
    validate_anchor_is_queue_head,
    validate_no_regroup,
    validate_remaining_queue,
    validate_repair_unit_coverage,
)
from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    FindingGroup,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.verify.repair_order.schemas import OrderedUnit, RepairOrderResult, SourceIdentity


def _finding(finding_id: str, source_tool: str) -> Finding:
    return Finding(
        finding_id=finding_id,
        source_tool=source_tool,  # type: ignore[arg-type]
        repair_workflow="WF_UNKNOWN_GATE",
    )


def _analysis() -> GroupErrorResult:
    return normalize_group_error_result(GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1"),
        input_integrity=InputIntegrity(status="COMPLETE"),
        finding_groups=[
            FindingGroup(
                group_id="TG-001",
                finding_ids=["F-0001", "F-0002"],
                matched_problem="x",
                match_basis="y",
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
    ))


def _repair_order() -> RepairOrderResult:
    return RepairOrderResult(
        status="SORTED",
        source_identity=SourceIdentity(change_id="c1"),
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="GROUP",
                unit_id="TG-001",
                finding_ids=["F-0001", "F-0002"],
                placement_rationale="first",
            ),
            OrderedUnit(
                position=2,
                unit_type="GROUP",
                unit_id="F-0003",
                finding_ids=["F-0003"],
                placement_rationale="second",
            ),
        ],
    )


def _diagnosed_anchor_only() -> DiagnosticResult:
    return DiagnosticResult(
        status="DIAGNOSED",
        source_identity=DiagnosticSourceIdentity(change_id="c1"),
        anchor_unit_id="TG-001",
        repair_unit=RepairUnit(
            unit_ids=["TG-001"],
            finding_ids=["F-0001", "F-0002"],
            affected_areas=["application_code"],
            root_cause_summary="stub cause",
            change_boundaries="stub boundaries",
        ),
        remaining_queue=[
            RemainingQueueUnit(position=2, unit_type="GROUP", unit_id="F-0003"),
        ],
    )


def test_validate_anchor_only_repair_unit():
    ro = _repair_order()
    result = _diagnosed_anchor_only()
    validate_anchor_is_queue_head(result, ro)
    validate_repair_unit_coverage(result, ro)
    validate_remaining_queue(result, ro)
    validate_no_regroup(result, ro, _analysis())


def test_validate_rejects_wrong_anchor():
    ro = _repair_order()
    result = _diagnosed_anchor_only().model_copy(update={"anchor_unit_id": "F-0003"})
    with pytest.raises(ValueError, match="first ordered unit"):
        validate_anchor_is_queue_head(result, ro)


def test_validate_requires_linkage_for_merged_units():
    ro = _repair_order()
    result = DiagnosticResult(
        status="DIAGNOSED",
        source_identity=DiagnosticSourceIdentity(change_id="c1"),
        anchor_unit_id="TG-001",
        repair_unit=RepairUnit(
            unit_ids=["TG-001", "F-0003"],
            finding_ids=["F-0001", "F-0002", "F-0003"],
            affected_areas=["typing"],
            root_cause_summary="shared",
            change_boundaries="both",
            merged_unit_linkages=[],
        ),
        remaining_queue=[],
    )
    with pytest.raises(ValueError, match="merged_unit_linkages"):
        validate_repair_unit_coverage(result, ro)
