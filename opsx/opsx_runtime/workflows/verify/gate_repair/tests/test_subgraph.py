"""Gate-repair subgraph smoke tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.state import GraphState
from agents.verify.group_error.agent import (
    GROUP_ERROR_PARTITION_MISMATCH_AFTER_GATES,
    GroupErrorAgent,
)
from agents.verify.repair_order.agent import RepairOrderAgent
from agents.verify.diagnostic.agent import DiagnosticAgent
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from workflows.verify.gate_repair import subgraph as gate_repair_subgraph
from workflows.verify.gate_repair.diagnostics import extract_gate_diagnostics
from workflows.verify.gate_repair.schemas import GateCheckResult, QualityGateRawBundle
from workflows.verify.tests.stub_adapters import (
    StubGroupErrorAdapter,
    StubRepairOrderAdapter,
    StubDiagnosticAdapter,
    StubStaticTriageAdapter,
    StubTypingRepairAdapter,
    StubTestTriageAdapter,
    StubTestRepairAdapter,
)


def _fail_bundle() -> QualityGateRawBundle:
    base = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=0,
        duration_ms=1,
    )
    return QualityGateRawBundle(
        pytest=base,
        pyright=base.model_copy(
            update={
                "check_id": "pyright",
                "exit_code": 1,
                "stdout_tail": "src/foo.py:10:5 - error: stub type error for subgraph test",
            },
        ),
        mypy=base.model_copy(
            update={
                "check_id": "mypy",
                "exit_code": 0,
                "stdout_tail": "Success: no issues found in 1 source file",
            },
        ),
    )


def _stub_agents():
    return (
        GroupErrorAgent(StubGroupErrorAdapter()),
        RepairOrderAgent(StubRepairOrderAdapter()),
        DiagnosticAgent(StubDiagnosticAdapter()),
        StaticTriageAgent(StubStaticTriageAdapter()),
        TypingRepairAgent(StubTypingRepairAdapter()),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(StubTestRepairAdapter()),
    )


def _pass_gate_patch() -> dict[str, object]:
    return {
        "quality_gate_verdict": "PASS",
        "quality_gate_diagnostics": {
            "total_count": 0,
            "pytest": [],
            "pyright": [],
            "mypy": [],
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
        },
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_marker": "✅ QUALITY_PASS",
    }


def _fail_gate_patch(bundle: QualityGateRawBundle) -> dict[str, object]:
    mypy_log = "Success: no issues found in 1 source file"
    diagnostics = extract_gate_diagnostics(bundle, mypy_parse_log=mypy_log)
    return {
        "quality_gate_verdict": "FAIL",
        "quality_gate_raw": bundle.model_dump(mode="json"),
        "quality_gate_diagnostics": diagnostics.as_handoff_dict(),
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_marker": "❌ QUALITY_FAIL",
    }


@pytest.mark.asyncio
async def test_gate_repair_subgraph_pass_skips_chain(tmp_path):
    """Чистые gates → ``STATIC_REPAIR_SKIP`` без ``group_error`` chain."""
    with patch.object(
        gate_repair_subgraph,
        "run_quality_gates_node",
        new=AsyncMock(return_value=_pass_gate_patch()),
    ):
        graph = gate_repair_subgraph.build_gate_repair_subgraph(*_stub_agents())
        result = await graph.ainvoke(
            {
                "repository_path": str(tmp_path),
                "workspace": str(tmp_path),
                "change_id": "pass-change",
            },
        )
    assert result["static_repair_marker"] == "✅ STATIC_REPAIR_SKIP"
    assert result.get("analysis_result") is None


@pytest.mark.asyncio
async def test_gate_repair_subgraph_group_error_pause_without_handoff(tmp_path):
    """Partition PAUSE без ``analysis_result`` → terminal PAUSE, без ``repair_order`` crash."""
    bundle = _fail_bundle()

    async def _pause_without_handoff(_state: GraphState) -> dict[str, object]:
        return {
            "group_error_execution_status": "PAUSE",
            "owner_attention": GROUP_ERROR_PARTITION_MISMATCH_AFTER_GATES,
        }

    group_error, repair_order, diagnostic, static_triage, typing_repair, test_triage, test_repair = (
        _stub_agents()
    )
    group_error.run = _pause_without_handoff  # type: ignore[method-assign]

    with patch.object(
        gate_repair_subgraph,
        "run_quality_gates_node",
        new=AsyncMock(return_value=_fail_gate_patch(bundle)),
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
                "change_id": "handoff-pause-change",
            },
        )

    assert result.get("verify_loop_terminal") is True
    assert result.get("verify_loop_status") == "PAUSE"
    owner = str(result.get("owner_attention") or "")
    assert GROUP_ERROR_PARTITION_MISMATCH_AFTER_GATES in owner
    assert result.get("analysis_result") is None
    assert result.get("repair_order_result") is None


@pytest.mark.asyncio
async def test_gate_repair_subgraph_default_group_error_reaches_repair_order(tmp_path):
    """Gate-only group_error (default) materializes ``analysis_result`` and repair_order."""
    bundle = _fail_bundle()
    with patch.object(
        gate_repair_subgraph,
        "run_quality_gates_node",
        new=AsyncMock(return_value=_fail_gate_patch(bundle)),
    ):
        graph = gate_repair_subgraph.build_gate_repair_subgraph(
            GroupErrorAgent(StubGroupErrorAdapter()),
            RepairOrderAgent(StubRepairOrderAdapter()),
            DiagnosticAgent(StubDiagnosticAdapter()),
            StaticTriageAgent(StubStaticTriageAdapter()),
            TypingRepairAgent(StubTypingRepairAdapter()),
            TestTriageAgent(StubTestTriageAdapter()),
            TestRepairAgent(StubTestRepairAdapter()),
            repair_adapter=None,
        )
        result = await graph.ainvoke(
            {
                "repository_path": str(tmp_path),
                "workspace": str(tmp_path),
                "change_id": "highway-change",
                "verify_outer_max_iterations": 1,
            },
        )

    assert isinstance(result.get("analysis_result"), dict)
    assert result.get("group_error_execution_status") == "deterministic_gate_only"
    ro = result.get("repair_order_result")
    assert isinstance(ro, dict)
    assert ro.get("status") == "SORTED"
    assert result.get("repair_order_execution_status") == "deterministic_baseline"


@pytest.mark.asyncio
async def test_gate_repair_subgraph_dispatch_cycle(tmp_path):
    """FAIL diagnostics → group_error → repair_order → diagnostic → dispatch_cycle."""
    bundle = _fail_bundle()
    with patch.object(
        gate_repair_subgraph,
        "run_quality_gates_node",
        new=AsyncMock(return_value=_fail_gate_patch(bundle)),
    ):
        graph = gate_repair_subgraph.build_gate_repair_subgraph(*_stub_agents(), repair_adapter=None)
        result = await graph.ainvoke(
            {
                "repository_path": str(tmp_path),
                "workspace": str(tmp_path),
                "change_id": "subgraph-change",
                "run_uuid": "subgraph-exec-001",
                "verify_outer_max_iterations": 1,
            },
        )
    assert result["quality_gate_verdict"] == "FAIL"
    assert result["verify_loop_status"] == "VERIFY_LOOP_CAP"
    assert result["verify_outer_iteration"] == 1
    assert result["analysis_result"]["agent"] == "group_error"
    assert result["repair_order_result"]["agent"] == "repair_order"
    assert result["diagnostic_result"]["agent"] == "diagnostic"
    assert result["dispatcher_cycle_status"] == "COMPLETE"
    assert result.get("group_error_execution_status") == "deterministic_gate_only"


def test_gate_repair_subgraph_nodes():
    """Subgraph: ``quality_gates`` → triage chain → dispatch_cycle."""
    graph = gate_repair_subgraph.build_gate_repair_subgraph(*_stub_agents())
    nodes = graph.get_graph().nodes
    assert "quality_gates" in nodes
    assert "static_repair" not in nodes
    assert "group_error" in nodes
    assert "group_error_handoff_terminal" in nodes
    assert "environment_prep" in nodes
    assert "repair_order" in nodes
    assert "diagnostic" in nodes
    assert "dispatch_cycle" in nodes


def _fail_collection_gate_patch() -> dict[str, object]:
    return {
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {
            "total_count": 2,
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
            "pytest": [
                "pytest: fixture 'repo_root' not found",
                "pytest: HermeticBoundaryError: production repo cannot be registered",
            ],
            "pyright": ["src/stroy/foo.py:10:5 - error: stub type error"],
            "mypy": [],
        },
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_marker": "❌ QUALITY_FAIL",
    }


class _StubEnvironmentPrepAgent:
    async def run(self, state: GraphState) -> dict[str, object]:
        _ = state
        return {
            "environment_prep_result": {
                "status": "NO_ACTION",
                "summary": "stub environment prep",
                "gates_rerun_recommended": False,
            },
            "environment_prep_attempts": 1,
            "environment_prep_execution_status": "stub",
        }


@pytest.mark.asyncio
async def test_gate_repair_subgraph_environment_prep_before_repair_order(tmp_path):
    """Collection FAIL → group_error → environment_prep → repair_order (до dispatch)."""
    agents = _stub_agents()
    with patch.object(
        gate_repair_subgraph,
        "run_quality_gates_node",
        new=AsyncMock(return_value=_fail_collection_gate_patch()),
    ):
        graph = gate_repair_subgraph.build_gate_repair_subgraph(
            *agents,
            environment_prep_agent=_StubEnvironmentPrepAgent(),
            repair_adapter=object(),
        )
        result = await graph.ainvoke(
            {
                "repository_path": str(tmp_path),
                "workspace": str(tmp_path),
                "change_id": "env-prep-change",
                "verify_outer_max_iterations": 1,
            },
        )
    assert result.get("environment_prep_attempts") == 1
    assert result.get("environment_prep_execution_status") == "stub"
    assert isinstance(result.get("repair_order_result"), dict)
