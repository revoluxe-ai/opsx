"""Связь dispatch batch recheck с родительским quality_gates."""

from __future__ import annotations

from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from workflows.verify.dispatch_cycle.subgraph import build_dispatch_cycle_subgraph
from workflows.verify.outer_loop import route_after_group_pass
from workflows.verify.tests.stub_adapters import (
    StubStaticTriageAdapter,
    StubTestRepairAdapter,
    StubTestTriageAdapter,
    StubTypingRepairAdapter,
)


def test_batch_recheck_parent_conditional_includes_quality_gates() -> None:
    assert (
        route_after_group_pass(
            {
                "verify_loop_terminal": False,
                "verify_outer_route_decision": "batch_recheck_gates",
            },
        )
        == "quality_gates"
    )


def test_dispatch_subgraph_compiled_contains_probe_nodes() -> None:
    graph = build_dispatch_cycle_subgraph(
        StaticTriageAgent(StubStaticTriageAdapter()),
        TypingRepairAgent(StubTypingRepairAdapter()),
        TestTriageAgent(StubTestTriageAdapter()),
        TestRepairAgent(StubTestRepairAdapter()),
        repair_adapter=None,
    )
    nodes = graph.get_graph().nodes
    assert "pytest_single_repro" in nodes
    assert "test_interaction" in nodes
    assert "batch_recheck_handoff" in nodes
