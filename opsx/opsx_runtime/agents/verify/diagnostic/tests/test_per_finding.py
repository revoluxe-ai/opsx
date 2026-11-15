"""Тесты детерминированного per-finding diagnostic."""

from __future__ import annotations

from agents.verify.diagnostic.per_finding import build_per_finding_diagnostic
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.verify.repair_order.schemas import OrderedUnit, RepairOrderResult, SourceIdentity


def test_per_finding_diagnostic_no_repair_unit():
    analysis = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1", identity_status="PARTIAL"),
        input_integrity=InputIntegrity(status="COMPLETE", issues=[]),
        ungrouped_finding_ids=["F-0001", "F-0002"],
        findings=[
            Finding(
                finding_id="F-0001",
                source_tool="pytest",
                observation="FAILED t.py::x",
                repair_workflow="WF_PYTEST_FAILURE",
            ),
            Finding(
                finding_id="F-0002",
                source_tool="pytest",
                observation="FAILED t.py::y",
                repair_workflow="WF_PYTEST_FAILURE",
            ),
        ],
        triage_summary=TriageSummary(findings_total=2, ungrouped_total=2),
    )
    ro = RepairOrderResult(
        status="SORTED",
        source_identity=SourceIdentity(change_id="c1", identity_status="PARTIAL"),
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
    d = build_per_finding_diagnostic(ro, analysis)
    assert d.repair_unit is None
    assert d.anchor_unit_id == "F-0001"
    assert len(d.remaining_queue) == 1
    assert d.remaining_queue[0].unit_id == "F-0002"
