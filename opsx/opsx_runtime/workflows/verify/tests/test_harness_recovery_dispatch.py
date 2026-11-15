"""Регрессия harness recovery: patch builder и decide_outer_route_after_unit."""

from __future__ import annotations

from typing import Any

import pytest

from agents.state import GraphState
from agents.verify.diagnostic.schemas import DiagnosticResult
from agents.verify.repair_order.schemas import FindingWorkflowRef, RepairOrderResult
from workflows.verify.outer_batch import (
    HARNESS_RECOVERY_NO_HARNESS_UNIT,
    build_harness_recovery_dispatch_patch,
    decide_outer_route_after_unit,
)
from workflows.verify.pytest_recovery import resolve_pytest_recovery_action


def _harness_workflow(finding_id: str = "F-0013") -> FindingWorkflowRef:
    return FindingWorkflowRef(
        finding_id=finding_id,
        source_tool="pytest",
        repair_workflow="WF_PYTEST_COLLECTION",
    )


def _non_harness_workflow(finding_id: str = "F-0001") -> FindingWorkflowRef:
    return FindingWorkflowRef(
        finding_id=finding_id,
        source_tool="pyright",
        repair_workflow="WF_PYRIGHT_TYPE",
    )


def _repair_order_json(
    units: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "agent": "repair_order",
        "status": "SORTED",
        "source_identity": {"identity_status": "PARTIAL"},
        "ordered_units": units,
    }


def _ordered_unit(
    *,
    position: int,
    unit_id: str,
    finding_ids: list[str],
    workflows: list[FindingWorkflowRef],
    unit_type: str = "GROUP",
) -> dict[str, Any]:
    return {
        "position": position,
        "unit_type": unit_type,
        "unit_id": unit_id,
        "finding_ids": finding_ids,
        "finding_workflows": [w.model_dump(mode="json") for w in workflows],
        "placement_rationale": "test",
    }


def test_harness_recovery_t1_patch_serializes() -> None:
    ro = RepairOrderResult.model_validate(
        _repair_order_json(
            [
                _ordered_unit(
                    position=1,
                    unit_id="E-0004",
                    finding_ids=["F-0013"],
                    workflows=[_harness_workflow()],
                ),
            ],
        ),
    )
    patch = build_harness_recovery_dispatch_patch(ro)
    assert patch
    assert patch["verify_outer_batch_active"] is True
    assert "repair_order_result" in patch
    assert "diagnostic_result" in patch
    DiagnosticResult.model_validate(patch["diagnostic_result"])


def test_harness_recovery_t2_no_harness_returns_empty() -> None:
    ro = RepairOrderResult.model_validate(
        _repair_order_json(
            [
                _ordered_unit(
                    position=1,
                    unit_id="F-0001",
                    finding_ids=["F-0001"],
                    workflows=[_non_harness_workflow()],
                ),
            ],
        ),
    )
    assert build_harness_recovery_dispatch_patch(ro) == {}


def test_harness_recovery_t3_two_harness_first_wins() -> None:
    ro = RepairOrderResult.model_validate(
        _repair_order_json(
            [
                _ordered_unit(
                    position=1,
                    unit_id="G-h2",
                    finding_ids=["F-h2"],
                    workflows=[_harness_workflow("F-h2")],
                ),
                _ordered_unit(
                    position=2,
                    unit_id="G-h1",
                    finding_ids=["F-h1"],
                    workflows=[_harness_workflow("F-h1")],
                ),
            ],
        ),
    )
    patch = build_harness_recovery_dispatch_patch(ro)
    units = patch["repair_order_result"]["ordered_units"]
    assert len(units) == 1
    assert units[0]["unit_id"] == "G-h2"


def test_harness_recovery_t4_stale_order_harness_not_first() -> None:
    ro = RepairOrderResult.model_validate(
        _repair_order_json(
            [
                _ordered_unit(
                    position=1,
                    unit_id="G-other",
                    finding_ids=["F-x"],
                    workflows=[_non_harness_workflow("F-x")],
                ),
                _ordered_unit(
                    position=2,
                    unit_id="E-0004",
                    finding_ids=["F-0013"],
                    workflows=[_harness_workflow()],
                ),
            ],
        ),
    )
    patch = build_harness_recovery_dispatch_patch(ro)
    assert patch["repair_order_result"]["ordered_units"][0]["unit_id"] == "E-0004"


def _harness_decide_state(
    repair_order: dict[str, Any],
    *,
    attempts: int = 0,
    analysis_harness: bool = True,
) -> GraphState:
    finding = {
        "finding_id": "F-0013",
        "source_tool": "pytest",
        "repair_workflow": "WF_PYTEST_COLLECTION",
        "observation": "ERROR collecting tests",
    }
    if not analysis_harness:
        finding["repair_workflow"] = "WF_PYRIGHT_TYPE"
        finding["source_tool"] = "pyright"
    return {
        "quality_gate_verdict": "FAIL",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {"total_count": 1},
        "verify_outer_iteration": 1,
        "verify_outer_max_iterations": 5,
        "verify_harness_recovery_attempts": attempts,
        "repair_order_result": repair_order,
        "analysis_result": {
            "analysis_identity": {"change_id": "c", "identity_status": "PARTIAL"},
            "input_integrity": {"status": "COMPLETE", "issues": []},
            "findings": [finding] if analysis_harness else [],
            "ungrouped_finding_ids": ["F-0013"] if analysis_harness else [],
            "triage_summary": {"findings_total": 1 if analysis_harness else 0, "ungrouped_total": 1},
        },
        "dispatcher_outcomes": [
            {
                "outcome": {
                    "verify_results": {
                        "member_statuses": [
                            {
                                "finding_id": "F-0013",
                                "status": "UNKNOWN",
                                "detail": "cohort",
                            },
                        ],
                    },
                },
            },
        ],
    }


def test_harness_recovery_t5_decide_harness_recovery() -> None:
    ro = _repair_order_json(
        [_ordered_unit(position=1, unit_id="E-0004", finding_ids=["F-0013"], workflows=[_harness_workflow()])],
    )
    decision, patch = decide_outer_route_after_unit(_harness_decide_state(ro))
    assert decision == "harness_recovery"
    assert patch.get("verify_outer_route_decision") == "harness_recovery"
    assert patch.get("verify_harness_recovery_attempts") == 1


def test_harness_recovery_t6_empty_patch_owner_attention() -> None:
    ro = _repair_order_json(
        [
            _ordered_unit(
                position=1,
                unit_id="F-0001",
                finding_ids=["F-0001"],
                workflows=[_non_harness_workflow()],
            ),
        ],
    )
    decision, patch = decide_outer_route_after_unit(_harness_decide_state(ro))
    assert decision == "terminal_pause"
    assert patch.get("owner_attention") == HARNESS_RECOVERY_NO_HARNESS_UNIT


def test_harness_recovery_t7_mismatch_analysis_harness_no_workflows() -> None:
    """T7 совпадает с T6: ENQUEUE по analysis, patch пуст без WF_PYTEST_COLLECTION в RO."""
    test_harness_recovery_t6_empty_patch_owner_attention()


def test_harness_recovery_t8_attempts_pause_not_enqueue() -> None:
    state: GraphState = {
        "verify_harness_recovery_attempts": 2,
        "analysis_result": {
            "findings": [
                {"finding_id": "F-0013", "repair_workflow": "WF_PYTEST_COLLECTION"},
            ],
        },
    }
    assert resolve_pytest_recovery_action(state) == "PAUSE"


def test_harness_recovery_t9_diagnostic_contract() -> None:
    ro = RepairOrderResult.model_validate(
        _repair_order_json(
            [
                _ordered_unit(
                    position=1,
                    unit_id="E-0004",
                    finding_ids=["F-0013", "F-extra"],
                    workflows=[_harness_workflow()],
                ),
            ],
        ),
    )
    head = ro.ordered_units[0]
    patch = build_harness_recovery_dispatch_patch(ro)
    diag = DiagnosticResult.model_validate(patch["diagnostic_result"])
    assert diag.repair_unit is not None
    assert list(diag.repair_unit.finding_ids) == list(head.finding_ids)
    assert diag.remaining_queue == []
