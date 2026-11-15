"""Per-finding dispatch: один finding в очереди, legacy repair_unit в repair_unit mode."""

from __future__ import annotations

import pytest

from agents.verify.dispatcher.queue import build_dispatcher_pass_queue
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.state import GraphState


def _state_one_pytest_finding() -> GraphState:
    analysis = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1", identity_status="PARTIAL"),
        input_integrity=InputIntegrity(status="COMPLETE", issues=[]),
        ungrouped_finding_ids=["F-0001"],
        findings=[
            Finding(
                finding_id="F-0001",
                source_tool="pytest",
                observation=(
                    "pytest: FAILED tests/x.py::test_t - AssertionError: wrong-test"
                ),
                repair_workflow="WF_PYTEST_FAILURE",
            ),
        ],
        triage_summary=TriageSummary(findings_total=1, ungrouped_total=1),
    )
    return {
        "verify_dispatch_mode": "per_finding",
        "analysis_result": analysis.model_dump(mode="json"),
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
                    "placement_rationale": "safe first",
                },
            ],
        },
    }


def test_per_finding_queue_single_item():
    queue, status = build_dispatcher_pass_queue(_state_one_pytest_finding())
    assert status == "READY_PER_FINDING"
    assert len(queue) == 1
    assert queue[0]["finding_id"] == "F-0001"


@pytest.mark.asyncio
async def test_per_finding_dispatch_skips_unit_group_triage(tmp_path):
    """Per-finding: single repro → triage → module pre-repair; без unit_group_triage."""
    from unittest.mock import AsyncMock, patch

    from agents.verify.static_triage.agent import StaticTriageAgent
    from agents.verify.test_repair.agent import TestRepairAgent
    from agents.verify.test_triage.agent import TestTriageAgent
    from agents.verify.typing_repair.agent import TypingRepairAgent
    from workflows.verify.dispatch_cycle.subgraph import build_dispatch_cycle_subgraph
    from workflows.verify.tests.stub_adapters import (
        StubStaticTriageAdapter,
        StubTestRepairAdapter,
        StubTestTriageAdapter,
        StubTypingRepairAdapter,
    )

    nodes: list[str] = []
    state = _state_one_pytest_finding()
    state["repository_path"] = str(tmp_path)
    state["workspace"] = str(tmp_path)
    probe_patch = {
        "pytest_single_repro_result": {
            "status": "SAME_FAIL",
            "duration_routing": False,
        },
        "verify_dispatch_path": "test",
    }
    triage_patch = {
        "test_triage_result": {"classification": "TEST", "finding_id": "F-0001"},
        "verify_dispatch_path": "test",
    }

    async def _fake_pre_repair(_state, **_kwargs):
        return {
            "verify_skip_test_repair": True,
            "pytest_execute_pre_repair_result": {"status": "PAUSE"},
        }

    with patch(
        "workflows.verify.dispatch_cycle.subgraph.run_pytest_single_repro_node",
        return_value=probe_patch,
    ), patch.object(TestTriageAgent, "run", AsyncMock(return_value=triage_patch)), patch(
        "workflows.verify.dispatch_cycle.pytest_execute_pre_repair.run_pytest_execute_pre_repair_node",
        side_effect=_fake_pre_repair,
    ):
        graph = build_dispatch_cycle_subgraph(
            StaticTriageAgent(StubStaticTriageAdapter()),
            TypingRepairAgent(StubTypingRepairAdapter()),
            TestTriageAgent(StubTestTriageAdapter()),
            TestRepairAgent(StubTestRepairAdapter()),
            repair_adapter=None,
        )
        async for ev in graph.astream(state):
            for node in ev:
                if node != "__end__":
                    nodes.append(node)

    assert "unit_group_triage" not in nodes
    assert "pytest_single_repro" in nodes
    assert "pytest_execute_pre_repair" in nodes
