"""DI-контейнер verify-hop: ``dependency_injector`` рядом с группой агентов.

Каждый hop-агент — отдельный ``providers.Factory``; вызываются по необходимости
(``container.impl_verify_agent()`` и т.д.), без сборки «связки``.

``common`` — ``DependenciesContainer``: с composition root подключают
``CommonAgentsContainer`` (apply + commit), ``apply_agent`` — ссылка на его провайдер.
См. dependency-injector «decoupled packages» / use-cases.
"""

from __future__ import annotations

from dependency_injector import containers, providers

from check_workflow_receipt import CheckWorkflowReceipt

from .agents import (
    ArchiveVerifyAgent,
    CoverageActivationAgent,
    ImplVerifyAgent,
    ScenarioCoverageVerifyAgent,
    ScenarioTestRepairAgent,
)


class VerifyHopContainer(containers.DeclarativeContainer):
    """Фабрики verify subgraph hop-агентов на одном адаптере."""

    adapter = providers.Dependency()
    receipt = providers.Callable(CheckWorkflowReceipt.none)

    impl_verify_agent = providers.Factory(
        ImplVerifyAgent,
        adapter=adapter,
        receipt=receipt,
    )
    scenario_coverage_agent = providers.Factory(
        ScenarioCoverageVerifyAgent,
        adapter=adapter,
        receipt=receipt,
    )
    coverage_activation_agent = providers.Factory(
        CoverageActivationAgent,
        adapter=adapter,
        receipt=receipt,
    )
    common = providers.DependenciesContainer()
    apply_agent = common.apply_agent
    scenario_test_repair_agent = providers.Factory(
        ScenarioTestRepairAgent,
        adapter=adapter,
        receipt=receipt,
    )
    archive_verify_agent = providers.Factory(
        ArchiveVerifyAgent,
        adapter=adapter,
        receipt=receipt,
    )
