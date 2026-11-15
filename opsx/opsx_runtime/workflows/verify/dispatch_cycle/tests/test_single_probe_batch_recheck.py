"""Graph/routing: одиночный probe и возврат на FULL_SUITE quality_gates."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agents.state import GraphState
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.test_interaction.agent import TestInteractionAgent
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from workflows.verify.dispatch_cycle.pytest_single_repro import classify_single_repro
from workflows.verify.dispatch_cycle.single_repro_probe_key import build_single_repro_probe_key
from workflows.verify.dispatch_cycle.subgraph import build_dispatch_cycle_subgraph
from workflows.verify.outer_batch import decide_outer_route_after_unit
from workflows.verify.tests.stub_adapters import (
    StubStaticTriageAdapter,
    StubTestRepairAdapter,
    StubTestTriageAdapter,
    StubTypingRepairAdapter,
)


def test_classify_single_repro_pass_requires_executed_call():
    log = (
        "OPSX_GATE_PROGRESS node=tests/unit/test_a.py::test_x "
        "when=call event=report outcome=passed\n"
    )
    assert (
        classify_single_repro(
            node_id="tests/unit/test_a.py::test_x",
            observation="pytest: FAILED tests/unit/test_a.py::test_x - AssertionError: bad",
            exit_code=0,
            log=log,
            gate_error=None,
        )
        == "PASS"
    )
    assert (
        classify_single_repro(
            node_id="tests/unit/test_a.py::test_x",
            observation="pytest: FAILED tests/unit/test_a.py::test_x - AssertionError: bad",
            exit_code=0,
            log="OPSX_GATE_PROGRESS node=tests/unit/test_a.py::test_x when=call event=report outcome=skipped\n",
            gate_error=None,
        )
        != "PASS"
    )


def test_classify_single_repro_pass_with_glued_pytest_progress_line():
    """pytest-terminals может склеить ``PASSED`` и ``OPSX_GATE_PROGRESS`` в одну строку."""
    node = "tests/unit/test_a.py::test_x"
    log = (
        "PASSED [100%]"
        "OPSX_GATE_PROGRESS node=tests/unit/test_a.py::test_x "
        "when=call event=report outcome=passed\n"
    )
    assert (
        classify_single_repro(
            node_id=node,
            observation=f"pytest: {node} - PYTEST_STALL",
            exit_code=0,
            log=log,
            gate_error=None,
        )
        == "PASS"
    )


def test_probe_key_changes_when_product_file_changes(tmp_path: Path) -> None:
    tests = tmp_path / "tests" / "unit"
    tests.mkdir(parents=True)
    test_file = tests / "test_a.py"
    test_file.write_text("def test_x(): assert 1\n", encoding="utf-8")
    product = tmp_path / "src" / "stroy" / "x.py"
    product.parent.mkdir(parents=True)
    product.write_text("value = 1\n", encoding="utf-8")
    obs = (
        "pytest: FAILED tests/unit/test_a.py::test_x - AssertionError: bad "
        'File "src/stroy/x.py", line 1'
    )
    node = "tests/unit/test_a.py::test_x"
    key_a = build_single_repro_probe_key(
        tmp_path, node_id=node, observation=obs, state={},
    )
    product.write_text("value = 2\n", encoding="utf-8")
    key_b = build_single_repro_probe_key(
        tmp_path, node_id=node, observation=obs, state={},
    )
    assert key_a != key_b


def test_decide_outer_route_batch_recheck_increments_and_forces_fresh():
    decision, patch = decide_outer_route_after_unit(
        {
            "verify_batch_recheck_required": True,
            "batch_recheck_count": 0,
            "verify_outer_max_iterations": 5,
            "quality_gate_diagnostics": {"pytest": ["pytest: FAILED x"], "total_count": 1},
        },
    )
    assert decision == "batch_recheck_gates"
    assert patch["batch_recheck_count"] == 1
    assert patch["verify_force_fresh_quality_gates"] is True
    assert patch["verify_batch_recheck_required"] is False


def test_batch_recheck_cap_uses_batch_recheck_count_not_outer_iteration():
    decision, patch = decide_outer_route_after_unit(
        {
            "verify_batch_recheck_required": True,
            "batch_recheck_count": 2,
            "verify_outer_iteration": 0,
            "verify_outer_max_iterations": 2,
        },
    )
    assert decision == "terminal_cap"
    assert patch["verify_loop_terminal"] is True
    assert patch.get("verify_outer_iteration", 0) == 0


@pytest.mark.asyncio
async def test_dispatch_graph_same_fail_probe_then_batch_recheck_handoff(tmp_path: Path):
    """SAME_FAIL probe → repair FIXED → batch_recheck_handoff без finalize."""
    state: GraphState = {
        "verify_dispatch_mode": "repair_unit",
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": "c1",
        "dispatcher_pass_initialized": True,
        "dispatcher_pass_queue": [
            {
                "finding_id": "F-0001",
                "observation": "pytest: FAILED tests/unit/test_a.py::test_x - AssertionError: bad",
                "repair_workflow": "WF_PYTEST_ASSERTION",
                "source_tool": "pytest",
            },
        ],
        "dispatcher_pass_index": 0,
        "dispatcher_pass_complete": False,
        "dispatcher_outcomes": [],
        "dispatcher_resolved_finding_ids": [],
    }
    probe_patch = {
        "pytest_single_repro_result": {
            "status": "SAME_FAIL",
            "node_id": "tests/unit/test_a.py::test_x",
            "duration_routing": False,
        },
        "verify_dispatch_path": "test",
    }
    triage_mock = AsyncMock(
        return_value={
            "test_triage_result": {"classification": "TEST", "finding_id": "F-0001"},
            "verify_dispatch_path": "test",
        },
    )
    repair_mock = AsyncMock(
        return_value={
            "verify_finding_fix": {
                "status": "FIXED",
                "files_changed": [],
                "summary": "noop",
            },
            "verify_dispatch_path": "test",
        },
    )
    with patch(
        "workflows.verify.dispatch_cycle.subgraph.run_pytest_single_repro_node",
        return_value=probe_patch,
    ), patch.object(TestTriageAgent, "run", triage_mock), patch.object(
        TestRepairAgent, "run", repair_mock,
    ):
        graph = build_dispatch_cycle_subgraph(
            StaticTriageAgent(StubStaticTriageAdapter()),
            TypingRepairAgent(StubTypingRepairAdapter()),
            TestTriageAgent(StubTestTriageAdapter()),
            TestRepairAgent(StubTestRepairAdapter()),
            repair_adapter=None,
        )
        result = await graph.ainvoke(state)
    assert result.get("verify_batch_recheck_required") is True
    assert result.get("verify_finding_finalize_outcome", {}).get("action") == (
        "BATCH_RECHECK_REQUIRED"
    )


@pytest.mark.asyncio
async def test_dispatch_graph_pass_probe_interaction_then_batch_recheck(tmp_path: Path):
    """PASS probe → test_interaction → repair → batch_recheck_handoff."""
    state: GraphState = {
        "verify_dispatch_mode": "repair_unit",
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "change_id": "c1",
        "dispatcher_pass_initialized": True,
        "dispatcher_pass_queue": [
            {
                "finding_id": "F-0002",
                "observation": "pytest: FAILED tests/unit/test_b.py::test_y - AssertionError: fluke",
                "repair_workflow": "WF_PYTEST_ASSERTION",
                "source_tool": "pytest",
            },
        ],
        "dispatcher_pass_index": 0,
        "dispatcher_pass_complete": False,
        "dispatcher_outcomes": [],
        "dispatcher_resolved_finding_ids": [],
    }
    probe_patch = {
        "pytest_single_repro_result": {
            "status": "PASS",
            "node_id": "tests/unit/test_b.py::test_y",
            "duration_routing": False,
        },
        "verify_dispatch_path": "test",
    }
    repair_mock = AsyncMock(
        return_value={
            "verify_finding_fix": {
                "status": "FIXED",
                "files_changed": [],
                "summary": "fixture isolation",
            },
            "verify_dispatch_path": "test",
        },
    )
    interaction_mock = AsyncMock(
        return_value={
            "test_interaction_result": {
                "finding_id": "F-0002",
                "classification": "ORDER",
                "repair_owner": "test_repair",
                "repro_request": "none",
                "rationale": "stub",
            },
            "verify_dispatch_path": "test",
            "test_interaction_execution_status": "finished",
        },
    )
    with patch(
        "workflows.verify.dispatch_cycle.subgraph.run_pytest_single_repro_node",
        return_value=probe_patch,
    ), patch.object(TestInteractionAgent, "run", interaction_mock), patch.object(
        TestRepairAgent, "run", repair_mock,
    ):
        graph = build_dispatch_cycle_subgraph(
            StaticTriageAgent(StubStaticTriageAdapter()),
            TypingRepairAgent(StubTypingRepairAdapter()),
            TestTriageAgent(StubTestTriageAdapter()),
            TestRepairAgent(StubTestRepairAdapter()),
            repair_adapter=None,
        )
        result = await graph.ainvoke(state)
    assert result.get("test_interaction_result", {}).get("repair_owner") == "test_repair"
    assert result.get("verify_batch_recheck_required") is True


@pytest.mark.asyncio
async def test_batch_recheck_handoff_marks_committed_finding_resolved_and_resets_stall():
    """COMMITTED batch recheck не оставляет finding в pending (иначе ложный unit_stall)."""
    from workflows.verify.dispatch_cycle.batch_recheck_handoff import run_batch_recheck_handoff

    state: GraphState = {
        "dispatcher_current_item": {"finding_id": "F-0001"},
        "commit_status": "COMMITTED",
        "verify_finding_commit_outcome": {"status": "COMMITTED", "finding_id": "F-0001"},
        "verify_finding_repair_outcome": {"status": "FIXED"},
        "dispatcher_resolved_finding_ids": [],
        "verify_outer_batch_stall_key": "F-0001",
        "verify_outer_batch_stall_count": 2,
        "dispatcher_pass_queue": [{}],
        "dispatcher_pass_index": 0,
        "dispatcher_outcomes": [],
    }
    patch = await run_batch_recheck_handoff(state)
    assert patch.get("dispatcher_resolved_finding_ids") == ["F-0001"]
    assert patch.get("verify_outer_batch_stall_key") is None
    assert patch.get("verify_outer_batch_stall_count") == 0
    assert patch.get("verify_batch_recheck_required") is True


@pytest.mark.asyncio
async def test_batch_recheck_handoff_defers_full_suite_while_per_finding_queue():
    """per_finding: чиним все findings, FULL_SUITE только после последнего COMMITTED."""
    from workflows.verify.dispatch_cycle.batch_recheck_handoff import run_batch_recheck_handoff

    state: GraphState = {
        "verify_dispatch_mode": "per_finding",
        "dispatcher_current_item": {"finding_id": "F-0001"},
        "commit_status": "COMMITTED",
        "verify_finding_commit_outcome": {"status": "COMMITTED", "finding_id": "F-0001"},
        "verify_finding_repair_outcome": {"status": "FIXED"},
        "dispatcher_resolved_finding_ids": [],
        "dispatcher_pass_queue": [{}, {}],
        "dispatcher_pass_index": 0,
        "dispatcher_outcomes": [],
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
                    "placement_rationale": "r1",
                },
                {
                    "position": 2,
                    "unit_type": "UNGROUPED",
                    "unit_id": "F-0002",
                    "finding_ids": ["F-0002"],
                    "placement_rationale": "r2",
                },
            ],
        },
    }
    patch = await run_batch_recheck_handoff(state)
    assert patch.get("dispatcher_resolved_finding_ids") == ["F-0001"]
    assert patch.get("verify_batch_recheck_required") is None
    assert patch.get("verify_finding_finalize_outcome", {}).get("action") == (
        "BATCH_RECHECK_DEFERRED_PENDING_FINDINGS"
    )
    assert patch.get("verify_finding_finalize_outcome", {}).get("pending_finding_ids") == [
        "F-0002",
    ]
