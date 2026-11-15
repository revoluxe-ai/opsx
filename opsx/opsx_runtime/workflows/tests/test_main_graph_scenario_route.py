"""Parent graph: planning и scenario stages на корне, не внутри implementation."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.implementation.apply.agent import ApplyAgent
from agents.repair.scenario_test_repair.agent import ScenarioTestRepairAgent
from agents.verify.archive_verify.agent import ArchiveVerifyAgent
from agents.verify.bugbot.agent import BugBotAgent
from agents.verify.coverage_activation.agent import CoverageActivationAgent
from agents.verify.diagnostic.agent import DiagnosticAgent
from agents.verify.group_error.agent import GroupErrorAgent
from agents.verify.impl_verify.agent import ImplVerifyAgent
from agents.verify.project_bug_hunt.agent import ProjectBugHuntAgent
from agents.verify.repair_order.agent import RepairOrderAgent
from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent
from agents.verify.security_review.agent import SecurityReviewAgent
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from workflows.implementation.graph import build_implementation_hop_agents
from workflows.main_graph import build_main_graph, build_main_implementation_only_graph


@pytest.mark.unit
def test_main_graph_with_hops_starts_at_planning() -> None:
    adapter = MagicMock()
    hop = build_implementation_hop_agents(adapter=adapter)
    stub = MagicMock()
    main = build_main_graph(
        ImplVerifyAgent(stub),
        ScenarioCoverageVerifyAgent(stub),
        CoverageActivationAgent(stub),
        ApplyAgent(stub),
        ScenarioTestRepairAgent(stub),
        GroupErrorAgent(stub),
        RepairOrderAgent(stub),
        DiagnosticAgent(stub),
        StaticTriageAgent(stub),
        TypingRepairAgent(stub),
        TestTriageAgent(stub),
        TestRepairAgent(stub),
        ProjectBugHuntAgent(stub),
        BugBotAgent(stub),
        SecurityReviewAgent(stub),
        ArchiveVerifyAgent(stub),
        stub,
        implementation_hop_agents=hop,
    )
    g = main.get_graph()
    starts = [e.target for e in g.edges if e.source == "__start__"]
    assert starts == ["planning"]
    nodes = set(g.nodes)
    assert {"planning", "scenario_trace_materialize"} <= nodes
    assert "scenario_test_authoring" not in nodes
    from workflows.implementation.graph import build_implementation_graph

    implementation_nodes = set(
        build_implementation_graph(hop, checkpointer=None).get_graph().nodes,
    )
    assert "scenario_test_authoring" in implementation_nodes
    assert "scenario_trace_materialize" not in implementation_nodes


@pytest.mark.unit
def test_verify_only_graph_has_no_planning_nodes() -> None:
    stub = MagicMock()
    main = build_main_graph(
        ImplVerifyAgent(stub),
        ScenarioCoverageVerifyAgent(stub),
        CoverageActivationAgent(stub),
        ApplyAgent(stub),
        ScenarioTestRepairAgent(stub),
        GroupErrorAgent(stub),
        RepairOrderAgent(stub),
        DiagnosticAgent(stub),
        StaticTriageAgent(stub),
        TypingRepairAgent(stub),
        TestTriageAgent(stub),
        TestRepairAgent(stub),
        ProjectBugHuntAgent(stub),
        BugBotAgent(stub),
        SecurityReviewAgent(stub),
        ArchiveVerifyAgent(stub),
        stub,
    )
    nodes = set(main.get_graph().nodes)
    assert "planning" not in nodes
    assert "scenario_test_authoring" not in nodes


@pytest.mark.unit
def test_implementation_only_includes_scenario_stages_no_verify() -> None:
    hop = build_implementation_hop_agents(adapter=MagicMock())
    main = build_main_implementation_only_graph(hop)
    nodes = set(main.get_graph().nodes)
    assert "verify" not in nodes
    assert {"planning", "scenario_trace_materialize"} <= nodes
    assert "scenario_test_authoring" not in nodes
