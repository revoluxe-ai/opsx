"""Тесты маршрутизации внешнего verify-цикла."""

from __future__ import annotations

import pytest

from agents.state import GraphState
from langgraph.graph import END

from workflows.verify.outer_loop import (
    DEFAULT_VERIFY_COHORT_MAX_RESTARTS,
    DEFAULT_VERIFY_OUTER_MAX_ITERATIONS,
    route_after_group_pass,
    route_after_quality_gates,
    route_after_reset_repair_batch,
    verify_after_group_pass_node,
)


def test_route_after_reset_repair_batch_full_restart() -> None:
    assert (
        route_after_reset_repair_batch({
            "verify_loop_terminal": False,
            "verify_post_reset_route": "full_restart",
        })
        == "verify_begin_outer_cycle"
    )


def test_route_after_reset_repair_batch_terminal_end() -> None:
    from workflows.stop_errors import VerifyStageStop

    with pytest.raises(VerifyStageStop):
        route_after_reset_repair_batch({"verify_loop_terminal": True})


def test_route_after_reset_repair_batch_strict_continue() -> None:
    assert (
        route_after_reset_repair_batch({
            "verify_loop_terminal": False,
            "verify_post_reset_route": None,
        })
        == "group_error"
    )


def test_default_cohort_max_restarts() -> None:
    assert DEFAULT_VERIFY_COHORT_MAX_RESTARTS == 3


def test_route_after_quality_gates_to_group_error_when_fail():
    state: GraphState = {
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {"total_count": 2, "pytest": ["x"], "pyright": [], "mypy": []},
    }
    assert route_after_quality_gates(state) == "group_error"


def test_route_after_quality_gates_final_pending_pass():
    state: GraphState = {
        "verify_outer_batch_final_pending": True,
        "quality_gate_verdict": "PASS",
        "quality_gate_diagnostics": {"total_count": 0},
        "quality_gates_execution_status": "DETERMINISTIC",
    }
    assert route_after_quality_gates(state) == "verify_mark_verify_ok"


def test_route_after_quality_gates_final_pending_fail():
    state: GraphState = {
        "verify_outer_batch_final_pending": True,
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {"total_count": 1},
        "quality_gates_execution_status": "DETERMINISTIC",
    }
    assert route_after_quality_gates(state) == "verify_reset_repair_batch"


def test_route_after_quality_gates_to_success_when_pass():
    state: GraphState = {
        "quality_gate_verdict": "PASS",
        "quality_gate_diagnostics": {"total_count": 0},
        "quality_gates_execution_status": "DETERMINISTIC",
    }
    assert route_after_quality_gates(state) == "verify_mark_verify_ok"


def test_route_after_quality_gates_missing_diagnostics_routes_group_error():
    """Без выполненных gates ``is_verify_clean`` ложно — идём в repair chain."""
    assert route_after_quality_gates({}) == "group_error"


def test_route_after_quality_gates_stale_fail_zero_findings_goes_ok():
    """FAIL без in-scope findings не должен застревать в group_error."""
    state: GraphState = {
        "quality_gate_verdict": "FAIL",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {
            "total_count": 0,
            "input_integrity_status": "COMPLETE",
            "pytest": [],
            "pyright": [],
            "mypy": [],
        },
        "quality_gate_raw": {
            "pytest": {"exit_code": 0},
            "pyright": {"exit_code": 1},
            "mypy": {"exit_code": 0},
        },
    }
    assert route_after_quality_gates(state) == "verify_mark_verify_ok"


@pytest.mark.asyncio
async def test_route_after_group_pass_next_unit():
    patch = await verify_after_group_pass_node(
        {
            "quality_gate_verdict": "FAIL",
            "quality_gate_diagnostics": {"total_count": 3},
            "verify_outer_iteration": 1,
            "verify_outer_max_iterations": 5,
            "repair_order_result": {
                "schema_version": "1.1",
                "agent": "repair_order",
                "status": "SORTED",
                "source_identity": {"identity_status": "PARTIAL"},
                "ordered_units": [
                    {
                        "position": 1,
                        "unit_type": "UNGROUPED",
                        "unit_id": "F-0001",
                        "finding_ids": ["F-0001"],
                        "placement_rationale": "r",
                    },
                    {
                        "position": 2,
                        "unit_type": "UNGROUPED",
                        "unit_id": "F-0002",
                        "finding_ids": ["F-0002"],
                        "placement_rationale": "r",
                    },
                ],
            },
            "diagnostic_result": {
                "schema_version": "1.0",
                "agent": "diagnostic",
                "status": "DIAGNOSED",
                "source_identity": {"identity_status": "PARTIAL"},
                "anchor_unit_id": "F-0001",
                "repair_unit": {
                    "unit_ids": ["F-0001"],
                    "finding_ids": ["F-0001"],
                    "root_cause_summary": "x",
                    "change_boundaries": "y",
                },
                "remaining_queue": [
                    {
                        "position": 2,
                        "unit_type": "UNGROUPED",
                        "unit_id": "F-0002",
                    },
                ],
            },
            "analysis_result": {
                "schema_version": "1.5",
                "analysis_identity": {"change_id": "c", "identity_status": "PARTIAL"},
                "input_integrity": {"status": "COMPLETE", "issues": []},
                "grouped_errors": [
                    {
                        "error_id": "F-0001",
                        "problem": "typing",
                        "match_basis": "a",
                        "instruments": ["pyright"],
                        "finding_ids": ["F-0001"],
                    },
                    {
                        "error_id": "F-0002",
                        "problem": "typing",
                        "match_basis": "b",
                        "instruments": ["pyright"],
                        "finding_ids": ["F-0002"],
                    },
                ],
                "findings": [
                    {
                        "finding_id": "F-0001",
                        "source_tool": "pyright",
                        "observation": "src/stroy/a.py:1:1 - error: e1",
                        "repair_workflow": "WF_PYRIGHT_TYPE",
                    },
                    {
                        "finding_id": "F-0002",
                        "source_tool": "pyright",
                        "observation": "src/stroy/b.py:1:1 - error: e2",
                        "repair_workflow": "WF_PYRIGHT_TYPE",
                    },
                ],
                "ungrouped_finding_ids": [],
                "triage_summary": {"findings_total": 2, "ungrouped_total": 0},
            },
            "quality_gates_execution_status": "DETERMINISTIC",
        },
    )
    merged: GraphState = {**patch, "verify_outer_route_decision": patch.get("verify_outer_route_decision")}
    assert patch.get("verify_outer_route_decision") == "next_unit"
    assert route_after_group_pass(merged) == "dispatch_cycle"


def test_route_after_group_pass_batch_recheck_to_quality_gates() -> None:
    state: GraphState = {
        "verify_loop_terminal": False,
        "verify_outer_route_decision": "batch_recheck_gates",
    }
    assert route_after_group_pass(state) == "quality_gates"


@pytest.mark.asyncio
async def test_verify_after_group_pass_batch_recheck_sets_decision() -> None:
    patch = await verify_after_group_pass_node(
        {
            "verify_batch_recheck_required": True,
            "batch_recheck_count": 0,
            "verify_outer_max_iterations": 3,
            "quality_gate_diagnostics": {"pytest": ["pytest: FAILED x"], "total_count": 1},
        },
    )
    assert patch["verify_outer_route_decision"] == "batch_recheck_gates"
    assert patch["batch_recheck_count"] == 1
    assert patch.get("verify_force_fresh_quality_gates") is True


def test_route_after_group_pass_loops_to_begin_on_invalidation():
    state: GraphState = {
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {"total_count": 1},
        "verify_outer_iteration": 2,
        "verify_loop_terminal": False,
        "verify_outer_route_decision": "full_cycle",
    }
    assert route_after_group_pass(state) == "verify_begin_outer_cycle"


@pytest.mark.asyncio
async def test_route_after_group_pass_to_end_on_cap():
    from langgraph.graph import END

    patch = await verify_after_group_pass_node(
        {
            "quality_gate_verdict": "FAIL",
            "quality_gate_diagnostics": {"total_count": 3},
            "verify_outer_iteration": DEFAULT_VERIFY_OUTER_MAX_ITERATIONS,
        },
    )
    assert patch["verify_loop_status"] == "VERIFY_LOOP_CAP"
    assert patch["verify_loop_terminal"] is True
    merged: GraphState = {
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {"total_count": 3},
        **patch,
        "verify_outer_iteration": DEFAULT_VERIFY_OUTER_MAX_ITERATIONS,
    }
    from workflows.stop_errors import VerifyStageStop

    with pytest.raises(VerifyStageStop):
        route_after_group_pass(merged)


@pytest.mark.asyncio
async def test_route_after_group_pass_to_skip_terminal_when_clean():
    patch = await verify_after_group_pass_node(
        {
            "quality_gate_verdict": "PASS",
            "quality_gate_diagnostics": {"total_count": 0},
            "quality_gates_execution_status": "DETERMINISTIC",
            "verify_outer_iteration": 1,
        },
    )
    assert patch["verify_loop_status"] == "VERIFY_OK"
    merged: GraphState = {
        **patch,
        "quality_gate_verdict": "PASS",
        "quality_gate_diagnostics": {"total_count": 0},
        "quality_gates_execution_status": "DETERMINISTIC",
    }
    assert route_after_group_pass(merged) == "verify_mark_verify_ok"
