"""Acceptance tests: outer repair batch (sequential units без повторного GroupError/RepairOrder)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from agents.state import GraphState
from agents.verify.diagnostic.schemas import RemainingQueueUnit
from agents.verify.repair_order.schemas import RepairOrderResult
from workflows.verify.gate_repair import subgraph as gate_repair_subgraph
from workflows.verify.gate_repair.schemas import GateCheckResult, QualityGateRawBundle
from workflows.verify.gate_repair.diagnostics import extract_gate_diagnostics
from workflows.verify.gate_repair.tests.test_subgraph import _pass_gate_patch, _stub_agents
from workflows.verify.outer_batch import (
    build_anchor_only_diagnostic,
    decide_outer_route_after_unit,
    filter_remaining_queue,
    queue_invalidation_reason,
    shrink_repair_order,
)


def _three_fail_bundle() -> QualityGateRawBundle:
    base = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=0,
        duration_ms=1,
    )
    # Static gate triage is intentionally limited to src/stroy/.
    # Outside this scope GroupError produces zero repairable findings.
    lines = [
        "src/stroy/a.py:10:1 - error: e1",
        "src/stroy/b.py:11:1 - error: e2",
        "src/stroy/c.py:12:1 - error: e3",
    ]
    return QualityGateRawBundle(
        pytest=base,
        pyright=base.model_copy(
            update={
                "check_id": "pyright",
                "exit_code": 1,
                "stdout_tail": "\n".join(lines),
            },
        ),
        mypy=base.model_copy(update={"check_id": "mypy", "exit_code": 0}),
    )


def _fail_patch(bundle: QualityGateRawBundle) -> dict[str, object]:
    diagnostics = extract_gate_diagnostics(bundle, mypy_parse_log="Success: no issues found")
    return {
        "quality_gate_verdict": "FAIL",
        "quality_gate_raw": bundle.model_dump(mode="json"),
        "quality_gate_diagnostics": diagnostics.as_handoff_dict(),
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_marker": "❌ QUALITY_FAIL",
    }


class _HopCounter:
    """Счётчики вызовов hop-агентов gate-repair chain."""

    def __init__(self) -> None:
        self.group_error = 0
        self.repair_order = 0
        self.diagnostic = 0
        self.quality_gates = 0


@pytest.mark.asyncio
async def test_t1_three_units_one_group_error_one_repair_order(tmp_path):
    """T1: три независимые units — один GroupError/RepairOrder, три dispatch, два gate-run."""
    bundle = _three_fail_bundle()
    # Guard the fixture itself: a failing full gate MUST yield three
    # in-scope repairable findings, not a vacuous empty queue.
    from agents.verify.group_error.findings_from_diagnostics import build_findings_from_diagnostics

    parsed = extract_gate_diagnostics(bundle, mypy_parse_log="Success: no issues found")
    assert len(build_findings_from_diagnostics(parsed.as_handoff_dict())) == 3
    counters = _HopCounter()
    agents = _stub_agents()
    group_error, repair_order, diagnostic, static_triage, typing_repair, test_triage, test_repair = (
        agents
    )

    orig_ge = group_error.run
    orig_ro = repair_order.run
    orig_dg = diagnostic.run

    async def ge_run(state: GraphState) -> dict[str, Any]:
        counters.group_error += 1
        return await orig_ge(state)

    async def ro_run(state: GraphState) -> dict[str, Any]:
        counters.repair_order += 1
        return await orig_ro(state)

    async def dg_run(state: GraphState) -> dict[str, Any]:
        counters.diagnostic += 1
        return await orig_dg(state)

    group_error.run = ge_run  # type: ignore[method-assign]
    repair_order.run = ro_run  # type: ignore[method-assign]
    diagnostic.run = dg_run  # type: ignore[method-assign]

    async def gates_node(state: GraphState) -> dict[str, Any]:
        counters.quality_gates += 1
        if state.get("verify_outer_batch_final_pending"):
            return _pass_gate_patch()
        return _fail_patch(bundle)

    with patch.object(
        gate_repair_subgraph,
        "run_quality_gates_node",
        new=AsyncMock(side_effect=gates_node),
    ), patch(
        "agents.verify.dispatch_unit.group_flow.verify_group_findings",
        side_effect=lambda _s, *, finding_ids, files_changed: [
            __import__(
                "agents.verify.dispatch_unit.schemas",
                fromlist=["FindingMemberStatus"],
            ).FindingMemberStatus(finding_id=fid, status="RESOLVED", detail="test")
            for fid in finding_ids
        ],
    ):
        graph = gate_repair_subgraph.build_gate_repair_subgraph(
            group_error,
            repair_order,
            diagnostic,
            static_triage,
            typing_repair,
            test_triage,
            test_repair,
            repair_adapter=None,
        )
        result = await graph.ainvoke(
            {
                "repository_path": str(tmp_path),
                "workspace": str(tmp_path),
                "change_id": "batch-t1",
                "verify_outer_max_iterations": 5,
            },
        )

    assert counters.group_error == 1
    assert counters.repair_order == 1
    assert counters.diagnostic == 1
    assert counters.quality_gates == 2
    assert result["verify_loop_status"] == "VERIFY_OK"
    # Do not weaken this receipt invariant: VERIFY_OK must preserve evidence
    # for all three repair units. Report adjacent state when LangGraph loses it.
    outcomes = result.get("dispatcher_outcomes") or []
    assert len(outcomes) == 3, {
        "observed_outcomes": len(outcomes),
        "dispatcher_pass_index": result.get("dispatcher_pass_index"),
        "dispatcher_pass_complete": result.get("dispatcher_pass_complete"),
        "dispatcher_pass_queue": result.get("dispatcher_pass_queue"),
        "dispatcher_resolved_finding_ids": result.get("dispatcher_resolved_finding_ids"),
        "verify_finding_finalize_outcome": result.get("verify_finding_finalize_outcome"),
        "diagnostic_remaining_queue": (
            (result.get("diagnostic_result") or {}).get("remaining_queue")
        ),
        "verify_outer_iteration": result.get("verify_outer_iteration"),
    }


@pytest.mark.asyncio
async def test_out_of_scope_pyright_failures_reconcile_to_verify_ok(tmp_path):
    """Pyright FAIL вне product scope не блокирует verify (нет repair unit)."""
    bundle = _three_fail_bundle()
    # Invalid synthetic paths deliberately fall outside the src/stroy/ scope.
    raw = bundle.pyright.model_copy(update={
        "stdout_tail": (
            "src/a.py:10:1 - error: out of product scope\\n"
            "src/b.py:11:1 - error: out of product scope"
        ),
    })
    bundle = bundle.model_copy(update={"pyright": raw})
    from agents.verify.group_error.findings_from_diagnostics import build_findings_from_diagnostics

    diagnostics = extract_gate_diagnostics(bundle, mypy_parse_log="Success: no issues found")
    assert build_findings_from_diagnostics(diagnostics.as_handoff_dict()) == []

    count = 0

    async def gates_node(state: GraphState) -> dict[str, Any]:
        nonlocal count
        count += 1
        # If a bug ever schedules final gates, this fixture would incorrectly
        # report success. It must never reach that second gate.
        if state.get("verify_outer_batch_final_pending"):
            return _pass_gate_patch()
        return _fail_patch(bundle)

    with patch.object(
        gate_repair_subgraph, "run_quality_gates_node",
        new=AsyncMock(side_effect=gates_node),
    ):
        graph = gate_repair_subgraph.build_gate_repair_subgraph(
            *_stub_agents(), repair_adapter=None,
        )
        result = await graph.ainvoke({
            "repository_path": str(tmp_path),
            "workspace": str(tmp_path),
            "change_id": "out-of-scope-failure",
            "verify_outer_max_iterations": 5,
        })

    assert result["verify_loop_status"] == "VERIFY_OK"
    assert result["verify_loop_terminal"] is True
    assert "REPAIR_HANDOFF_PAUSED" not in str(result.get("owner_attention") or "")
    assert count == 1
    assert result.get("dispatcher_outcomes") == []


def test_stale_skipped_diagnostic_routes_final_gates_after_per_finding_resolve():
    """После commit единственного finding не PAUSE на устаревшем diagnostic."""
    node = "tests/unit/foo.py::test_bar"
    ro = {
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
        ],
    }
    decision, patch = decide_outer_route_after_unit(
        {
            "verify_dispatch_mode": "per_finding",
            "quality_gate_verdict": "FAIL",
            "quality_gate_diagnostics": {
                "pytest": [f"pytest: {node} - PYTEST_SUITE_TIMEOUT"],
                "input_integrity_status": "PARTIAL",
                "input_integrity_issues": [],
                "quality_gate_raw": {"pytest": {"exit_code": 1}},
                "total_count": 1,
            },
            "quality_gates_execution_status": "DETERMINISTIC",
            "verify_outer_iteration": 1,
            "verify_outer_max_iterations": 5,
            "group_error_execution_status": "skipped_input_integrity",
            "repair_order_result": ro,
            "diagnostic_result": {
                "status": "PAUSE",
                "pause_reason": (
                    "group_error hop skipped due to incomplete or invalid diagnostics input; "
                    "diagnostic requires input_integrity COMPLETE"
                ),
                "source_identity": {"identity_status": "PARTIAL"},
                "anchor_unit_id": "",
                "repair_unit": None,
                "remaining_queue": [],
            },
            "dispatcher_resolved_finding_ids": ["F-0001"],
            "analysis_result": {
                "analysis_identity": {"change_id": "c1"},
                "input_integrity": {"status": "PARTIAL", "issues": []},
                "findings": [
                    {
                        "finding_id": "F-0001",
                        "source_tool": "pytest",
                        "repair_workflow": "WF_UNKNOWN_GATE",
                    },
                ],
                "finding_groups": [],
                "ungrouped_finding_ids": ["F-0001"],
                "triage_summary": {
                    "findings_total": 1,
                    "finding_groups_total": 0,
                    "ungrouped_total": 1,
                },
            },
        },
    )
    assert decision == "final_gates"
    assert patch.get("verify_outer_batch_final_pending") is True
    assert patch.get("verify_loop_status") == "IN_PROGRESS"


def test_failed_gate_with_paused_diagnostic_never_routes_final_gates():
    decision, patch = decide_outer_route_after_unit({
        "quality_gate_verdict": "FAIL",
        "quality_gates_execution_status": "DETERMINISTIC",
        "verify_outer_iteration": 1,
        "verify_outer_max_iterations": 5,
        "repair_order_result": {
            "status": "PAUSE",
            "source_identity": {"identity_status": "PARTIAL"},
            "pause_reason": "no actionable findings",
            "ordered_units": [],
        },
        "diagnostic_result": {
            "status": "PAUSE",
            "source_identity": {"identity_status": "PARTIAL"},
            "anchor_unit_id": "",
            "repair_unit": None,
            "remaining_queue": [],
            "pause_reason": "repair_order paused",
        },
    })
    assert decision == "terminal_pause"
    assert patch["verify_loop_status"] == "PAUSE"
    assert "REPAIR_HANDOFF_PAUSED" in patch["owner_attention"]


def test_t2_cascading_resolution_drops_remaining_unit():
    """T2: полностью resolved unit исчезает из remaining."""
    ro = RepairOrderResult.model_validate(
        {
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
    )
    remaining = [
        RemainingQueueUnit(position=2, unit_type="UNGROUPED", unit_id="F-0002"),
    ]
    filtered, pause = filter_remaining_queue(remaining, ro, {"F-0002"})
    assert pause is None
    assert filtered == []


@pytest.mark.asyncio
async def test_t3_partial_repair_pause():
    """T3: частичный repair unit в remaining → PAUSE."""
    ro = RepairOrderResult.model_validate(
        {
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
                    "unit_type": "GROUP",
                    "unit_id": "G-1",
                    "finding_ids": ["F-0002", "F-0003"],
                    "placement_rationale": "r",
                },
            ],
        },
    )
    remaining = [
        RemainingQueueUnit(position=2, unit_type="GROUP", unit_id="G-1"),
    ]
    _, pause = filter_remaining_queue(remaining, ro, {"F-0002"}, per_finding=False)
    assert pause is not None

    decision, patch = decide_outer_route_after_unit(
        {
            "verify_dispatch_mode": "repair_unit",
            "quality_gate_verdict": "FAIL",
            "quality_gate_diagnostics": {"total_count": 2},
            "quality_gates_execution_status": "DETERMINISTIC",
            "verify_outer_iteration": 1,
            "verify_outer_max_iterations": 5,
            "repair_order_result": ro.model_dump(mode="json"),
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
                "remaining_queue": [r.model_dump(mode="json") for r in remaining],
            },
            "dispatcher_resolved_finding_ids": ["F-0002"],
            "analysis_result": {
                "analysis_identity": {"change_id": "c", "identity_status": "PARTIAL"},
                "input_integrity": {"status": "COMPLETE", "issues": []},
                "findings": [],
                "ungrouped_finding_ids": [],
                "triage_summary": {"findings_total": 3, "ungrouped_total": 1},
            },
        },
    )
    assert decision == "terminal_pause"
    assert patch.get("verify_loop_status") == "PAUSE"


def test_t4_queue_invalidation_on_conftest():
    """T4: правка conftest → full_cycle invalidation."""
    reason = queue_invalidation_reason(
        {},
        files_changed={"tests/conftest.py"},
        next_unit=RemainingQueueUnit(position=2, unit_type="UNGROUPED", unit_id="F-0002"),
    )
    assert reason is not None
    assert "conftest" in reason


def test_t5_unknown_freshness_pause():
    """T5: UNKNOWN member status блокирует blind repair."""
    decision, patch = decide_outer_route_after_unit(
        {
            "verify_dispatch_mode": "repair_unit",
            "quality_gate_verdict": "FAIL",
            "quality_gate_diagnostics": {"total_count": 1},
            "quality_gates_execution_status": "DETERMINISTIC",
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
                    {"position": 2, "unit_type": "UNGROUPED", "unit_id": "F-0002"},
                ],
            },
            "analysis_result": {
                "schema_version": "1.5",
                "analysis_identity": {"change_id": "c", "identity_status": "PARTIAL"},
                "input_integrity": {"status": "COMPLETE", "issues": []},
                "grouped_errors": [
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
                        "finding_id": "F-0002",
                        "source_tool": "pyright",
                        "observation": "src/b.py:1:1 - error: e",
                        "repair_workflow": "WF_PYRIGHT_TYPE",
                    },
                ],
                "ungrouped_finding_ids": [],
                "triage_summary": {"findings_total": 1, "ungrouped_total": 0},
            },
            "dispatcher_outcomes": [
                {
                    "workflow_kind": "repair_unit",
                    "outcome": {
                        "kind": "repair_unit",
                        "verify_results": {
                            "member_statuses": [
                                {
                                    "finding_id": "F-0002",
                                    "status": "UNKNOWN",
                                    "detail": "stale",
                                },
                            ],
                        },
                    },
                },
            ],
        },
    )
    assert decision == "terminal_pause"


def test_t9_final_gates_fail_no_verify_ok():
    """T9: финальные gates FAIL → не VERIFY_OK."""
    decision, _ = decide_outer_route_after_unit(
        {
            "verify_dispatch_mode": "repair_unit",
            "quality_gate_verdict": "FAIL",
            "quality_gate_diagnostics": {"total_count": 1},
            "quality_gates_execution_status": "DETERMINISTIC",
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
                "remaining_queue": [],
            },
            "dispatcher_resolved_finding_ids": ["F-0001"],
        },
    )
    assert decision == "final_gates"


def test_t10_anchor_only_diagnostic_preserves_remaining():
    """T10: batch diagnostic не ломает partition remaining."""
    ro = RepairOrderResult.model_validate(
        {
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
    )
    shrunk = shrink_repair_order(
        ro,
        [RemainingQueueUnit(position=2, unit_type="UNGROUPED", unit_id="F-0002")],
    )
    diag = build_anchor_only_diagnostic(shrunk)
    assert diag.anchor_unit_id == "F-0002"
    assert diag.remaining_queue == []
