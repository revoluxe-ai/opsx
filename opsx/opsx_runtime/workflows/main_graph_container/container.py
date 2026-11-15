"""DI composition root parent graph (pilot): verify + implementation lanes.

Вложенные ``DependenciesContainer`` (decoupled packages dependency-injector):

- ``adapter_providers`` — ``BridgeContainer`` / ``AdaptersContainer`` (review + apply adapters)
- ``verify_hop`` — ``agents.verify.verify_hop.VerifyHopContainer`` (+ ``common``)
- ``implementation_hop`` — ``workflows.implementation.implementation_hop`` (опционально)

Отдельно: gate-repair и review-агенты через ``providers.Dependency()`` —
composition root заполняет после ручного или будущего gate-repair DI.

``build_main_graph`` — единственная фабрика compiled parent graph; без
``run_implement_then_verify_workflow`` (lifecycle/checkpoint остаётся в ``main_graph``).

Пример wiring (pyright-smoke уровень)::

    from agents.adapters.<provider>.containers import AdaptersContainer, BridgeContainer
    from agents.common.container import CommonAgentsContainer
    from agents.verify.verify_hop import VerifyHopContainer
    from workflows.implementation.implementation_hop import (
        ImplementationHopWorkflowContainer,
        assemble_implementation_hop_agents,
    )
    from workflows.main_graph_container import MainGraphWorkflowContainer

    # bridge = BridgeContainer(); await init_resources(); bridge_runtime = await bridge.bridge_runtime()
    adapters = AdaptersContainer()
    bundle = adapters.bundle(bridge=bridge_runtime)

    common = CommonAgentsContainer()
    common.adapter.override(bundle.apply_adapter)

    verify_hop = VerifyHopContainer()
    verify_hop.adapter.override(bundle.review_adapter)
    verify_hop.common.override(common)

    impl_lane = ImplementationHopWorkflowContainer()
    impl_lane.adapter.override(bundle.apply_adapter)
    hop_agents = assemble_implementation_hop_agents(
        apply_design=impl_lane.apply_design_agent(),
        apply_design_review=impl_lane.apply_design_review_agent(),
        apply_scaffold=impl_lane.apply_scaffold_agent(),
        apply_red=impl_lane.apply_red_agent(),
        apply_implement=impl_lane.apply_implement_agent(),
        apply_green=impl_lane.apply_green_agent(),
    )

    parent = MainGraphWorkflowContainer()
    parent.adapter_providers.override(adapters)  # или узкий shim с review_adapter/apply_adapter
    parent.verify_hop.override(verify_hop)
    parent.implementation_hop_agents.override(hop_agents)
    parent.repair_adapter.override(bundle.apply_adapter)
    # parent.group_error_agent.override(...)  # gate repair deps
    graph = parent.build_main_graph()
"""

from __future__ import annotations

from bug_log.null_journal import NullBugJournal
from dependency_injector import containers, providers

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
from workflows.implementation.graph import ImplementationHopAgents
from workflows.main_graph import build_main_graph


class MainGraphWorkflowContainer(containers.DeclarativeContainer):
    """Фабрика ``build_main_graph``; hop-агенты — через ``verify_hop`` и Dependency."""

    adapter_providers = providers.DependenciesContainer()
    bug_journal = providers.Object(NullBugJournal())
    verify_hop = providers.DependenciesContainer()
    implementation_hop = providers.DependenciesContainer()

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
    implementation_hop_agents = providers.Dependency(
        default=None,
        instance_of=ImplementationHopAgents,
    )
    include_verify_subgraph = providers.Object(True)
    checkpointer = providers.Dependency(default=None)

    build_main_graph = providers.Factory(
        build_main_graph,
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
        repair_adapter=repair_adapter,
        implementation_hop_agents=implementation_hop_agents,
        include_verify_subgraph=include_verify_subgraph,
        checkpointer=checkpointer,
    )
