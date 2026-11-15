"""Tests for DiagnosticAgent input package."""

from __future__ import annotations

from agents.verify.diagnostic.input_pack import (
    build_diagnostic_package,
    diagnostic_findings_artifact_path,
)
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.verify.repair_order.schemas import OrderedUnit, RepairOrderResult, SourceIdentity


def test_build_diagnostic_package_anchor_is_first_unit():
    analysis = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1"),
        input_integrity=InputIntegrity(status="COMPLETE"),
        ungrouped_finding_ids=["F-0001", "F-0002"],
        findings=[
            Finding(
                finding_id="F-0001",
                source_tool="pytest",
                repair_workflow="WF_UNKNOWN_GATE",
            ),
            Finding(
                finding_id="F-0002",
                source_tool="mypy",
                repair_workflow="WF_UNKNOWN_GATE",
            ),
        ],
        triage_summary=TriageSummary(findings_total=2, ungrouped_total=2),
    )
    repair_order = RepairOrderResult(
        status="SORTED",
        source_identity=SourceIdentity(change_id="c1"),
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="UNGROUPED",
                unit_id="F-0001",
                finding_ids=["F-0001"],
                placement_rationale="a",
            ),
            OrderedUnit(
                position=2,
                unit_type="UNGROUPED",
                unit_id="F-0002",
                finding_ids=["F-0002"],
                placement_rationale="b",
            ),
        ],
    )
    embed = {"findings": [], "ordered_units": []}
    package = build_diagnostic_package(
        repair_order,
        analysis,
        findings_embed=embed,
    )
    assert package["anchor"]["unit_id"] == "F-0001"
    assert len(package["remaining_ordered_units_index"]) == 1
    assert package["remaining_ordered_units_index"][0]["unit_id"] == "F-0002"
    assert "findings_embed" in package
    assert "ordered_units_index" in package

    artifact_path = diagnostic_findings_artifact_path(
        repository_path=".",
        change_id="c1",
        stream_log="opsx/logs/c1/deadbeef.log",
    )
    assert artifact_path.name == "deadbeef-findings.json"
