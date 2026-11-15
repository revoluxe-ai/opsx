"""Тесты очереди Dispatcher pass (repair unit level)."""

from __future__ import annotations

from agents.state import GraphState
from agents.verify.dispatcher.queue import build_dispatcher_pass_queue
from agents.verify.diagnostic.schemas import (
    DiagnosticResult,
    DiagnosticSourceIdentity,
    RepairUnit,
)
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)


def _analysis(*finding_ids: str) -> dict:
    findings = [
        Finding(
            finding_id=fid,
            source_tool="pyright",
            observation=f"src/a.py:10:1 - error: stub {fid}",
        )
        for fid in finding_ids
    ]
    body = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1", identity_status="PARTIAL"),
        input_integrity=InputIntegrity(status="COMPLETE", issues=[]),
        ungrouped_finding_ids=list(finding_ids),
        findings=findings,
        triage_summary=TriageSummary(
            findings_total=len(findings),
            ungrouped_total=len(findings),
        ),
    )
    return body.model_dump(mode="json")


def test_queue_per_finding_head_only():
    state: GraphState = {
        "verify_dispatch_mode": "per_finding",
        "analysis_result": _analysis("F-0001", "F-0002"),
        "repair_order_result": {
            "schema_version": "1.1",
            "agent": "repair_order",
            "status": "SORTED",
            "source_identity": {
                "change_id": "c1",
                "identity_status": "PARTIAL",
                "group_error_schema_version": "1.4",
            },
            "ordered_units": [
                {
                    "position": 1,
                    "unit_type": "UNGROUPED",
                    "unit_id": "F-0001",
                    "finding_ids": ["F-0001"],
                    "placement_rationale": "a",
                },
                {
                    "position": 2,
                    "unit_type": "UNGROUPED",
                    "unit_id": "F-0002",
                    "finding_ids": ["F-0002"],
                    "placement_rationale": "b",
                },
            ],
        },
        "diagnostic_result": {
            "schema_version": "1.0",
            "agent": "diagnostic",
            "status": "DIAGNOSED",
            "source_identity": {
                "identity_status": "PARTIAL",
                "repair_order_schema_version": "1.1",
            },
            "anchor_unit_id": "F-0001",
            "repair_unit": None,
            "remaining_queue": [],
        },
    }
    queue, status = build_dispatcher_pass_queue(state)
    assert status == "READY_PER_FINDING"
    assert len(queue) == 1
    assert queue[0]["finding_id"] == "F-0001"
    assert queue[0].get("item_kind") != "repair_unit"


def test_queue_from_diagnostic_repair_unit_single_item():
    state: GraphState = {
        "verify_dispatch_mode": "repair_unit",
        "analysis_result": _analysis("F-0001", "F-0002"),
        "diagnostic_result": DiagnosticResult(
            status="DIAGNOSED",
            source_identity=DiagnosticSourceIdentity(identity_status="PARTIAL"),
            anchor_unit_id="F-0001",
            repair_unit=RepairUnit(
                unit_ids=["F-0001"],
                finding_ids=["F-0001", "F-0002"],
                root_cause_summary="x",
                change_boundaries="y",
            ),
        ).model_dump(mode="json"),
    }
    queue, status = build_dispatcher_pass_queue(state)
    assert status == "READY"
    assert len(queue) == 1
    assert queue[0]["item_kind"] == "repair_unit"
    assert queue[0]["finding_ids"] == ["F-0001", "F-0002"]


def test_queue_fallback_first_ordered_unit():
    state: GraphState = {
        "analysis_result": _analysis("F-0001"),
        "repair_order_result": {
            "schema_version": "1.1",
            "agent": "repair_order",
            "status": "SORTED",
            "source_identity": {
                "change_id": "c1",
                "identity_status": "PARTIAL",
                "group_error_schema_version": "1.4",
            },
            "ordered_units": [
                {
                    "position": 1,
                    "unit_type": "UNGROUPED",
                    "unit_id": "F-0001",
                    "finding_ids": ["F-0001"],
                    "placement_rationale": "stub",
                },
            ],
        },
    }
    queue, status = build_dispatcher_pass_queue(state)
    assert status in ("READY_FALLBACK_ORDER", "READY_PER_FINDING")
    assert len(queue) == 1
    assert queue[0]["finding_id"] == "F-0001"
