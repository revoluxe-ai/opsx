"""DI-контейнер verify lane: ``build_verify_graph`` и hop cross-links.

``verify_hop`` — ``DependenciesContainer``: с composition root подключают
``agents.verify.verify_hop.VerifyHopContainer`` (impl_verify, scenario coverage, …,
``apply_agent`` через вложенный ``common``).

Gate-repair и read-only review-агенты — отдельные ``providers.Dependency()``;
composition root передаёт их после ``build_gate_repair_hop_agents`` / review wiring.

``repair_adapter`` — mutating adapter для gate_repair subgraph (обычно apply_adapter).

Пример wiring::

    from agents.common.container import CommonAgentsContainer
    from agents.verify.verify_hop import VerifyHopContainer
    from workflows.verify.verify_workflow import VerifyWorkflowContainer

    verify_hop = VerifyHopContainer()
    verify_hop.adapter.override(review_adapter)

    common = CommonAgentsContainer()
    common.adapter.override(apply_adapter)
    verify_hop.common.override(common)

    verify_wf = VerifyWorkflowContainer()
    verify_wf.verify_hop.override(verify_hop)
    verify_wf.repair_adapter.override(apply_adapter)
    verify_wf.group_error_agent.override(group_error_agent)
    ...
    compiled = verify_wf.build_verify_graph()
"""

from __future__ import annotations

from bug_log.null_journal import NullBugJournal
from dependency_injector import containers, providers

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
from agents.repair.scenario_test_repair.agent import ScenarioTestRepairAgent
from agents.implementation.apply.agent import ApplyAgent
from workflows.verify.graph import build_verify_graph


class VerifyWorkflowContainer(containers.DeclarativeContainer):
    """Фабрика compiled verify-subgraph; hop-агенты — через ``verify_hop`` и Dependency."""

    verify_hop = providers.DependenciesContainer()
    bug_journal = providers.Object(NullBugJournal())

    impl_verify_agent: providers.Provider[ImplVerifyAgent] = verify_hop.impl_verify_agent
    scenario_coverage_agent: providers.Provider[ScenarioCoverageVerifyAgent] = (
        verify_hop.scenario_coverage_agent
    )
    coverage_activation_agent: providers.Provider[CoverageActivationAgent] = (
        verify_hop.coverage_activation_agent
    )
    apply_agent: providers.Provider[ApplyAgent] = verify_hop.apply_agent
    scenario_test_repair_agent: providers.Provider[ScenarioTestRepairAgent] = (
        verify_hop.scenario_test_repair_agent
    )
    archive_verify_agent: providers.Provider[ArchiveVerifyAgent] = verify_hop.archive_verify_agent

    group_error_agent = providers.Dependency(instance_of=GroupErrorAgent)
    repair_order_agent = providers.Dependency(instance_of=RepairOrderAgent)
    diagnostic_agent = providers.Dependency(instance_of=DiagnosticAgent)
    static_triage_agent = providers.Dependency(instance_of=StaticTriageAgent)
    typing_repair_agent = providers.Dependency(instance_of=TypingRepairAgent)
    test_triage_agent = providers.Dependency(instance_of=TestTriageAgent)
    test_repair_agent = providers.Dependency(instance_of=TestRepairAgent)
    project_bug_hunt_agent = providers.Dependency(instance_of=ProjectBugHuntAgent)
    bugbot_agent = providers.Dependency(instance_of=BugBotAgent)
    security_review_agent = providers.Dependency(instance_of=SecurityReviewAgent)

    repair_adapter = providers.Dependency()
    checkpointer = providers.Dependency(default=None)

    build_verify_graph = providers.Factory(
        build_verify_graph,
        impl_agent=impl_verify_agent,
        scenario_coverage_agent=scenario_coverage_agent,
        coverage_activation_agent=coverage_activation_agent,
        apply_agent=apply_agent,
        scenario_test_repair_agent=scenario_test_repair_agent,
        group_error_agent=group_error_agent,
        repair_order_agent=repair_order_agent,
        diagnostic_agent=diagnostic_agent,
        static_triage_agent=static_triage_agent,
        typing_repair_agent=typing_repair_agent,
        test_triage_agent=test_triage_agent,
        test_repair_agent=test_repair_agent,
        project_bug_hunt_agent=project_bug_hunt_agent,
        bugbot_agent=bugbot_agent,
        security_review_agent=security_review_agent,
        archive_verify_agent=archive_verify_agent,
        adapter=repair_adapter,
        checkpointer=checkpointer,
    )
