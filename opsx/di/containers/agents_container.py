"""Контейнер бандлов LangGraph hop-агентов verify-impl (dependency-injector)."""

from __future__ import annotations

from pathlib import Path

from dependency_injector import containers, providers

from opsx.di._runtime_imports import ensure_opsx_runtime_importable
from opsx.di.types import (
    GateRepairHopAgentBundle,
    ReviewAgentBundle,
    VerifyHopAgentBundle,
    VerifyImplAdapters,
)


def _build_verify_hop_agents(
    repo_root: Path,
    adapters: VerifyImplAdapters,
) -> VerifyHopAgentBundle:
    """Собрать агентов verify subgraph."""
    ensure_opsx_runtime_importable(repo_root=repo_root)
    from workflows.main_graph import build_verify_hop_agents

    (
        impl_agent,
        scenario_coverage_agent,
        coverage_activation_agent,
        apply_agent,
        scenario_test_repair_agent,
        archive_verify_agent,
    ) = build_verify_hop_agents(
        review_adapter=adapters.review_adapter,
        apply_adapter=adapters.apply_adapter,
    )
    return VerifyHopAgentBundle(
        impl_agent=impl_agent,
        scenario_coverage_agent=scenario_coverage_agent,
        coverage_activation_agent=coverage_activation_agent,
        apply_agent=apply_agent,
        scenario_test_repair_agent=scenario_test_repair_agent,
        archive_verify_agent=archive_verify_agent,
    )


def _build_gate_repair_hop_agents(
    repo_root: Path,
    adapters: VerifyImplAdapters,
) -> GateRepairHopAgentBundle:
    """Собрать агентов gate repair subgraph."""
    ensure_opsx_runtime_importable(repo_root=repo_root)
    from workflows.main_graph import build_gate_repair_hop_agents

    (
        group_error_agent,
        repair_order_agent,
        diagnostic_agent,
        static_triage_agent,
        typing_repair_agent,
        test_triage_agent,
        test_repair_agent,
        harness_repair_agent,
        pytest_classifier_agent,
    ) = build_gate_repair_hop_agents(
        review_adapter=adapters.review_adapter,
        repair_adapter=adapters.apply_adapter,
    )
    return GateRepairHopAgentBundle(
        group_error_agent=group_error_agent,
        repair_order_agent=repair_order_agent,
        diagnostic_agent=diagnostic_agent,
        static_triage_agent=static_triage_agent,
        typing_repair_agent=typing_repair_agent,
        test_triage_agent=test_triage_agent,
        test_repair_agent=test_repair_agent,
        harness_repair_agent=harness_repair_agent,
        pytest_classifier_agent=pytest_classifier_agent,
    )


def _build_implementation_hop_agents(
    repo_root: Path,
    adapters: VerifyImplAdapters,
) -> object:
    """Собрать ``ImplementationHopAgents`` (apply_design … apply_green)."""
    ensure_opsx_runtime_importable(repo_root=repo_root)
    from workflows.main_graph import build_implementation_hop_agents

    return build_implementation_hop_agents(apply_adapter=adapters.apply_adapter)


def _build_review_agents(
    repo_root: Path,
    adapters: VerifyImplAdapters,
) -> ReviewAgentBundle:
    """Собрать BugBot / ProjectBugHunt / SecurityReview."""
    ensure_opsx_runtime_importable(repo_root=repo_root)
    from agents.verify.bugbot.agent import BugBotAgent
    from agents.verify.project_bug_hunt.agent import ProjectBugHuntAgent
    from agents.verify.security_review.agent import SecurityReviewAgent

    return ReviewAgentBundle(
        project_bug_hunt_agent=ProjectBugHuntAgent(adapters.review_adapter),
        bugbot_agent=BugBotAgent(adapters.review_adapter),
        security_review_agent=SecurityReviewAgent(adapters.review_adapter),
    )


class AgentsContainer(containers.DeclarativeContainer):
    """Фабрики ``build_*_hop_agents`` и read-only review-агентов."""

    repo_root = providers.Object(Path())

    verify_hop = providers.Factory(
        _build_verify_hop_agents,
        repo_root=repo_root,
        adapters=providers.Dependency(),
    )
    gate_repair_hop = providers.Factory(
        _build_gate_repair_hop_agents,
        repo_root=repo_root,
        adapters=providers.Dependency(),
    )
    implementation_hop = providers.Factory(
        _build_implementation_hop_agents,
        repo_root=repo_root,
        adapters=providers.Dependency(),
    )
    review = providers.Factory(
        _build_review_agents,
        repo_root=repo_root,
        adapters=providers.Dependency(),
    )
