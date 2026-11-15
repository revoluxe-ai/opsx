"""DI-контейнер implementation lane: subgraph и split-APPLY hop-агенты.

Каждый hop-агент — отдельный ``providers.Factory``; ``build_implementation_graph``
и ``build_main_implementation_only_graph`` — отдельные фабрики без «связки агентов».

``adapter`` — один mutating ``ABCAdapter`` (composition root: ``adapter_providers`` /
``AdaptersContainer.bundle().apply_adapter``).

Пример wiring (без запуска graph)::

    from workflows.implementation.implementation_hop import (
        ImplementationHopWorkflowContainer,
        assemble_implementation_hop_agents,
    )

    hop = ImplementationHopWorkflowContainer()
    hop.adapter.override(apply_adapter)
    agents = assemble_implementation_hop_agents(
        apply_design=hop.apply_design_agent(),
        apply_design_review=hop.apply_design_review_agent(),
        apply_scaffold=hop.apply_scaffold_agent(),
        apply_red=hop.apply_red_agent(),
        apply_implement=hop.apply_implement_agent(),
        apply_green=hop.apply_green_agent(),
    )
    hop.implementation_hop_agents.override(agents)
    graph = hop.build_implementation_graph()
"""

from __future__ import annotations

from dependency_injector import containers, providers

from agents.adapters.abc_adapter import ABCAdapter
from check_workflow_receipt import CheckWorkflowReceipt

from agents.implementation.apply_design.agent import ApplyDesignAgent
from agents.implementation.apply_design_review.agent import ApplyDesignReviewAgent
from agents.implementation.apply_green.agent import ApplyGreenAgent
from agents.implementation.apply_implement.agent import ApplyImplementAgent
from agents.implementation.apply_red.agent import ApplyRedAgent
from agents.implementation.apply_scaffold.agent import ApplyScaffoldAgent
from agents.implementation.implementation_arbiter.agent import ImplementationArbiterAgent
from agents.implementation.scenario_test_author.agent import ScenarioTestAuthorAgent
from agents.planning.scenario_coverage_plan.agent import ScenarioCoveragePlanAgent
from workflows.implementation.graph import (
    ImplementationHopAgents,
    build_implementation_graph,
)
from workflows.main_graph import build_main_implementation_only_graph


def assemble_implementation_hop_agents(
    *,
    apply_design: ApplyDesignAgent,
    apply_design_review: ApplyDesignReviewAgent,
    apply_scaffold: ApplyScaffoldAgent,
    apply_red: ApplyRedAgent,
    apply_implement: ApplyImplementAgent,
    apply_green: ApplyGreenAgent,
    implementation_arbiter: ImplementationArbiterAgent,
    adapter: ABCAdapter,
) -> ImplementationHopAgents:
    """Собрать frozen dataclass hop-агентов из отдельных фабрик контейнера."""
    return ImplementationHopAgents(
        apply_design=apply_design,
        apply_design_review=apply_design_review,
        apply_scaffold=apply_scaffold,
        apply_red=apply_red,
        apply_implement=apply_implement,
        apply_green=apply_green,
        implementation_arbiter=implementation_arbiter,
        scenario_coverage_plan=ScenarioCoveragePlanAgent(adapter),
        scenario_test_author=ScenarioTestAuthorAgent(adapter),
    )


class ImplementationHopWorkflowContainer(containers.DeclarativeContainer):
    """Фабрики implementation subgraph и split-APPLY hop-агентов на одном адаптере."""

    adapter = providers.Dependency()
    receipt = providers.Callable(CheckWorkflowReceipt.none)

    apply_design_agent = providers.Factory(
        ApplyDesignAgent,
        adapter=adapter,
        receipt=receipt,
    )
    apply_design_review_agent = providers.Factory(
        ApplyDesignReviewAgent,
        adapter=adapter,
        receipt=receipt,
    )
    apply_scaffold_agent = providers.Factory(
        ApplyScaffoldAgent,
        adapter=adapter,
        receipt=receipt,
    )
    apply_red_agent = providers.Factory(
        ApplyRedAgent,
        adapter=adapter,
        receipt=receipt,
    )
    apply_implement_agent = providers.Factory(
        ApplyImplementAgent,
        adapter=adapter,
        receipt=receipt,
    )
    apply_green_agent = providers.Factory(
        ApplyGreenAgent,
        adapter=adapter,
        receipt=receipt,
    )
    implementation_arbiter_agent = providers.Factory(
        ImplementationArbiterAgent,
        adapter=adapter,
        receipt=receipt,
    )

    implementation_hop_agents = providers.Dependency(instance_of=ImplementationHopAgents)
    checkpointer = providers.Dependency(default=None)

    build_implementation_graph = providers.Factory(
        build_implementation_graph,
        implementation_hop_agents=implementation_hop_agents,
        checkpointer=checkpointer,
    )
    build_main_implementation_only_graph = providers.Factory(
        build_main_implementation_only_graph,
        implementation_hop_agents=implementation_hop_agents,
        checkpointer=checkpointer,
    )
