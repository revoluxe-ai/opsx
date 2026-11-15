"""Тесты enrichment finding_workflows на OrderedUnit."""

from __future__ import annotations

from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.verify.repair_order.enrichment import attach_finding_workflows
from agents.verify.repair_order.schemas import OrderedUnit, RepairOrderResult, SourceIdentity


def test_attach_finding_workflows_copies_from_analysis():
    analysis = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1", identity_status="PARTIAL"),
        input_integrity=InputIntegrity(status="COMPLETE", issues=[]),
        findings=[
            Finding(
                finding_id="F-0001",
                source_tool="mypy",
                observation="x.py:1: name-defined",
            ),
            Finding(
                finding_id="F-0002",
                source_tool="pyright",
                observation="x.py:1: error",
            ),
        ],
        ungrouped_finding_ids=["F-0001", "F-0002"],
        triage_summary=TriageSummary(findings_total=2, ungrouped_total=2),
    )
    order = RepairOrderResult(
        status="SORTED",
        source_identity=SourceIdentity(change_id="c1", identity_status="PARTIAL"),
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="UNGROUPED",
                unit_id="F-0001",
                finding_ids=["F-0001"],
                placement_rationale="stub",
            ),
            OrderedUnit(
                position=2,
                unit_type="UNGROUPED",
                unit_id="F-0002",
                finding_ids=["F-0002"],
                placement_rationale="stub",
            ),
        ],
    )
    enriched = attach_finding_workflows(order, analysis)
    assert enriched.ordered_units[0].finding_workflows[0].repair_workflow == "WF_MYPY_CONTRACT"
    assert enriched.ordered_units[1].finding_workflows[0].repair_workflow == "WF_PYRIGHT_TYPE"
