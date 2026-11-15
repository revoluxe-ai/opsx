"""Главный parent graph OPSX: композиция и runtime orchestration.

``build_main_graph`` — корень LangGraph (optional ``implementation`` → ``verify``).
``run_workflow`` / ``run_implement_then_verify_workflow`` — сборка агентов, checkpoint,
вызов скомпилированного parent graph.

Точка входа из корня репозитория: ``poetry run python -u run_opsx_main.py <change_id>``
(``run_verify_impl.execute`` → ``workflows.main_graph.run_implement_then_verify_workflow``).

По умолчанию durable LangGraph checkpoint **отключён** (см.
``checkpoint_resume.durable_langgraph_checkpoint_enabled``).
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any, Callable

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from agents.adapters.abc_adapter import ABCAdapter
from agents.implementation.apply.agent import ApplyAgent
from agents.state import GraphState
from agents.verify.impl_verify.agent import ImplVerifyAgent
from agents.verify.coverage_activation.agent import CoverageActivationAgent
from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent
from agents.verify.bugbot.agent import BugBotAgent
from agents.verify.diagnostic.agent import DiagnosticAgent
from agents.verify.group_error.agent import GroupErrorAgent
from agents.verify.project_bug_hunt.agent import ProjectBugHuntAgent
from agents.verify.repair_order.agent import RepairOrderAgent
from agents.verify.archive_verify.agent import ArchiveVerifyAgent
from agents.verify.security_review.agent import SecurityReviewAgent
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.repair.scenario_test_repair.agent import ScenarioTestRepairAgent
from agents.verify.pytest_error_classifier.agent import PytestErrorClassifierAgent
from agents.verify.test_harness_repair.agent import TestHarnessRepairAgent
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from workflows.checkpoint_resume import (
    ResolvedWorkflowRun,
    WorkflowResumeScope,
    default_run_dir,
    durable_langgraph_checkpoint_enabled,
    invoke_checkpointed_graph,
    invoke_ephemeral_workflow_graph,
    list_checkpoint_candidates,
    score_checkpoint_thread,
    stable_thread_id,
)
from workflows.implementation.graph import (
    ImplementationHopAgents,
    build_implementation_graph,
    build_implementation_hop_agents,
)
from workflows.repo_paths import graph_repo_path_patch, resolve_opsx_repository_root
from workflows.planning.graph import build_planning_graph, route_after_planning
from workflows.scenario_parent.nodes import parent_scenario_trace_materialize_node
from workflows.scenario_parent.routing import (
    route_after_implementation_lane,
    route_after_scenario_trace_materialize,
)
from workflows.verify.graph import build_verify_graph


def build_main_graph(
    impl_agent: ImplVerifyAgent,
    scenario_coverage_agent: ScenarioCoverageVerifyAgent,
    coverage_activation_agent: CoverageActivationAgent,
    apply_agent: ApplyAgent,
    scenario_test_repair_agent: ScenarioTestRepairAgent,
    group_error_agent: GroupErrorAgent,
    repair_order_agent: RepairOrderAgent,
    diagnostic_agent: DiagnosticAgent,
    static_triage_agent: StaticTriageAgent,
    typing_repair_agent: TypingRepairAgent,
    test_triage_agent: TestTriageAgent,
    test_repair_agent: TestRepairAgent,
    project_bug_hunt_agent: ProjectBugHuntAgent,
    bugbot_agent: BugBotAgent,
    security_review_agent: SecurityReviewAgent,
    archive_verify_agent: ArchiveVerifyAgent,
    repair_adapter: ABCAdapter,
    *,
    implementation_hop_agents: ImplementationHopAgents | None = None,
    include_verify_subgraph: bool = True,
    checkpointer=None,
):
    """Собрать главный OPSX-граф.

    Без ``implementation_hop_agents``: ``START → verify → END`` (legacy verify-only).
    С ``implementation_hop_agents`` и ``include_verify_subgraph=True``:
    ``START → planning → implementation (authoring после apply-design) →
    scenario_trace_materialize → verify → END`` после ``APPLY_COMPLETE``.
    С hop agents и ``include_verify_subgraph=False``: тот же маршрут без verify.
    """
    builder = StateGraph(GraphState)

    if implementation_hop_agents is None:
        verify_graph = build_verify_graph(
            impl_agent,
            scenario_coverage_agent,
            coverage_activation_agent,
            apply_agent,
            scenario_test_repair_agent,
            group_error_agent,
            repair_order_agent,
            diagnostic_agent,
            static_triage_agent,
            typing_repair_agent,
            test_triage_agent,
            test_repair_agent,
            project_bug_hunt_agent,
            bugbot_agent,
            security_review_agent,
            archive_verify_agent,
            repair_adapter,
            checkpointer=checkpointer,
        )
        builder.add_node("verify", verify_graph)
        _ = builder.add_edge(START, "verify")
        _ = builder.add_edge("verify", END)
        return builder.compile(checkpointer=checkpointer)

    planning_graph = build_planning_graph(
        implementation_hop_agents.scenario_coverage_plan,
        checkpointer=checkpointer,
    )
    implementation_graph = build_implementation_graph(
        implementation_hop_agents,
        checkpointer=checkpointer,
    )
    builder.add_node("planning", planning_graph)
    builder.add_node("implementation", implementation_graph)
    builder.add_node("scenario_trace_materialize", parent_scenario_trace_materialize_node)
    _ = builder.add_edge(START, "planning")
    _ = builder.add_conditional_edges(
        "planning",
        route_after_planning,
        {
            "implementation": "implementation",
            END: END,
        },
    )
    _ = builder.add_conditional_edges(
        "implementation",
        route_after_implementation_lane,
        {
            "scenario_trace_materialize": "scenario_trace_materialize",
            END: END,
        },
    )
    materialize_router = partial(
        route_after_scenario_trace_materialize,
        include_verify=include_verify_subgraph,
    )

    if not include_verify_subgraph:
        _ = builder.add_conditional_edges(
            "scenario_trace_materialize",
            materialize_router,
            {END: END},
        )
        return builder.compile(checkpointer=checkpointer)

    verify_graph = build_verify_graph(
        impl_agent,
        scenario_coverage_agent,
        coverage_activation_agent,
        apply_agent,
        scenario_test_repair_agent,
        group_error_agent,
        repair_order_agent,
        diagnostic_agent,
        static_triage_agent,
        typing_repair_agent,
        test_triage_agent,
        test_repair_agent,
        project_bug_hunt_agent,
        bugbot_agent,
        security_review_agent,
        archive_verify_agent,
        repair_adapter,
        checkpointer=checkpointer,
    )
    builder.add_node("verify", verify_graph)
    _ = builder.add_conditional_edges(
        "scenario_trace_materialize",
        materialize_router,
        {
            END: END,
            "verify": "verify",
        },
    )
    _ = builder.add_edge("verify", END)
    return builder.compile(checkpointer=checkpointer)


def build_main_implementation_only_graph(
    implementation_hop_agents: ImplementationHopAgents,
    *,
    checkpointer=None,
):
    """``START → planning → implementation → materialize → END``."""
    from unittest.mock import MagicMock

    stub = MagicMock(name="verify_stub_adapter")
    return build_main_graph(
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
        implementation_hop_agents=implementation_hop_agents,
        include_verify_subgraph=False,
        checkpointer=checkpointer,
    )


def build_verify_hop_agents(
    *,
    review_adapter: ABCAdapter,
    apply_adapter: ABCAdapter,
) -> tuple[
    ImplVerifyAgent,
    ScenarioCoverageVerifyAgent,
    CoverageActivationAgent,
    ApplyAgent,
    ScenarioTestRepairAgent,
    ArchiveVerifyAgent,
]:
    """Собрать impl_verify, scenario coverage, coverage activation, apply, scenario test repair, archive verify."""
    return (
        ImplVerifyAgent(review_adapter),
        ScenarioCoverageVerifyAgent(review_adapter),
        CoverageActivationAgent(apply_adapter),
        ApplyAgent(apply_adapter),
        ScenarioTestRepairAgent(apply_adapter),
        ArchiveVerifyAgent(review_adapter),
    )


def build_gate_repair_hop_agents(
    *,
    review_adapter: ABCAdapter,
    repair_adapter: ABCAdapter,
) -> tuple[
    GroupErrorAgent,
    RepairOrderAgent,
    DiagnosticAgent,
    StaticTriageAgent,
    TypingRepairAgent,
    TestTriageAgent,
    TestRepairAgent,
    TestHarnessRepairAgent,
    PytestErrorClassifierAgent,
]:
    """Агенты цепочки group_error → repair_order → diagnostic → dispatch_cycle."""
    return (
        GroupErrorAgent(review_adapter),
        RepairOrderAgent(review_adapter),
        DiagnosticAgent(review_adapter),
        StaticTriageAgent(review_adapter),
        TypingRepairAgent(repair_adapter),
        TestTriageAgent(repair_adapter),
        TestRepairAgent(repair_adapter),
        TestHarnessRepairAgent(repair_adapter),
        PytestErrorClassifierAgent(review_adapter),
    )


def _build_graph_factory(
    impl_agent: ImplVerifyAgent,
    scenario_coverage_agent: ScenarioCoverageVerifyAgent,
    coverage_activation_agent: CoverageActivationAgent,
    apply_agent: ApplyAgent,
    scenario_test_repair_agent: ScenarioTestRepairAgent,
    group_error_agent: GroupErrorAgent,
    repair_order_agent: RepairOrderAgent,
    diagnostic_agent: DiagnosticAgent,
    static_triage_agent: StaticTriageAgent,
    typing_repair_agent: TypingRepairAgent,
    test_triage_agent: TestTriageAgent,
    test_repair_agent: TestRepairAgent,
    project_bug_hunt_agent: ProjectBugHuntAgent,
    bugbot_agent: BugBotAgent,
    security_review_agent: SecurityReviewAgent,
    archive_verify_agent: ArchiveVerifyAgent,
    repair_adapter: ABCAdapter,
    *,
    implementation_hop_agents=None,
) -> Callable[[Any], Any]:
    """Собрать фабрику ``build_main_graph(checkpointer=...)`` для resume scoring."""

    def factory(checkpointer: Any) -> Any:
        return build_main_graph(
            impl_agent,
            scenario_coverage_agent,
            coverage_activation_agent,
            apply_agent,
            scenario_test_repair_agent,
            group_error_agent,
            repair_order_agent,
            diagnostic_agent,
            static_triage_agent,
            typing_repair_agent,
            test_triage_agent,
            test_repair_agent,
            project_bug_hunt_agent,
            bugbot_agent,
            security_review_agent,
            archive_verify_agent,
            repair_adapter,
            implementation_hop_agents=implementation_hop_agents,
            checkpointer=checkpointer,
        )

    return factory


async def _run_compiled_main_graph(
    graph_factory: Callable[[Any], Any],
    *,
    initial_state: GraphState,
    repo_root: Path,
    thread_id: str,
    resume_scope: WorkflowResumeScope | None,
    run_dir: Path | None,
    checkpoint_path: Path | None,
) -> GraphState:
    """Собрать parent graph и выполнить: ephemeral или durable checkpoint."""
    if not durable_langgraph_checkpoint_enabled():
        graph = graph_factory(MemorySaver())
        effective_thread = thread_id or str(initial_state.get("run_uuid") or "")
        return await invoke_ephemeral_workflow_graph(
            graph,
            initial_state=initial_state,
            repo_root=repo_root,
            thread_id=effective_thread,
        )

    if resume_scope is not None:
        resolved = await _resolve_durable_run(
            graph_factory,
            repo_root=repo_root,
            scope=resume_scope,
        )
        checkpoint_path = resolved.checkpoint_path
        thread_id = resolved.thread_id
        run_dir = resolved.run_dir
    elif checkpoint_path is None:
        if run_dir is None:
            raise ValueError("run_dir or checkpoint_path is required")
        checkpoint_path = run_dir / "checkpoints.sqlite"

    checkpoint_path = checkpoint_path.resolve()
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        graph = graph_factory(saver)
        return await invoke_checkpointed_graph(
            graph,
            initial_state=initial_state,
            thread_id=thread_id,
            repo_root=repo_root,
        )


async def _resolve_durable_run(
    graph_factory: Callable[[Any], Any],
    *,
    repo_root: Path,
    scope: WorkflowResumeScope,
) -> ResolvedWorkflowRun:
    """Выбрать checkpoint/thread с максимальным durable progress в scope."""
    run_dir = default_run_dir(repo_root, scope)
    run_dir.mkdir(parents=True, exist_ok=True)
    candidates = list_checkpoint_candidates(repo_root, scope)
    if not candidates:
        canonical_ckpt = run_dir / "checkpoints.sqlite"
        return ResolvedWorkflowRun(
            run_dir=run_dir,
            checkpoint_path=canonical_ckpt,
            thread_id=stable_thread_id(scope),
            resumed=False,
        )

    best: ResolvedWorkflowRun | None = None
    best_score = -1
    for ckpt_path, thread_id in candidates:
        async with AsyncSqliteSaver.from_conn_string(str(ckpt_path)) as saver:
            graph = graph_factory(saver)
            scored = await score_checkpoint_thread(graph, thread_id=thread_id)
        if scored is None:
            continue
        score, next_nodes = scored
        if score <= best_score:
            continue
        best_score = score
        best = ResolvedWorkflowRun(
            run_dir=ckpt_path.parent,
            checkpoint_path=ckpt_path,
            thread_id=thread_id,
            resumed=bool(next_nodes),
        )

    if best is not None:
        return best

    canonical_ckpt = run_dir / "checkpoints.sqlite"
    return ResolvedWorkflowRun(
        run_dir=run_dir,
        checkpoint_path=canonical_ckpt,
        thread_id=stable_thread_id(scope),
        resumed=False,
    )


async def run_workflow(
    initial_state: GraphState,
    *,
    repo_root: Path,
    impl_agent: ImplVerifyAgent,
    scenario_coverage_agent: ScenarioCoverageVerifyAgent,
    coverage_activation_agent: CoverageActivationAgent,
    apply_agent: ApplyAgent,
    scenario_test_repair_agent: ScenarioTestRepairAgent,
    group_error_agent: GroupErrorAgent,
    repair_order_agent: RepairOrderAgent,
    diagnostic_agent: DiagnosticAgent,
    static_triage_agent: StaticTriageAgent,
    typing_repair_agent: TypingRepairAgent,
    test_triage_agent: TestTriageAgent,
    test_repair_agent: TestRepairAgent,
    project_bug_hunt_agent: ProjectBugHuntAgent,
    bugbot_agent: BugBotAgent,
    security_review_agent: SecurityReviewAgent,
    archive_verify_agent: ArchiveVerifyAgent,
    repair_adapter: ABCAdapter,
    run_dir: Path | None = None,
    checkpoint_path: Path | None = None,
    thread_id: str,
    resume_scope: WorkflowResumeScope | None = None,
) -> GraphState:
    """Выполнить parent graph (verify-only lane) через ``build_main_graph``."""
    repo_root = resolve_opsx_repository_root(repo_root)
    initial_state = {**initial_state, **graph_repo_path_patch(initial_state, repo_root)}
    graph_factory = _build_graph_factory(
        impl_agent,
        scenario_coverage_agent,
        coverage_activation_agent,
        apply_agent,
        scenario_test_repair_agent,
        group_error_agent,
        repair_order_agent,
        diagnostic_agent,
        static_triage_agent,
        typing_repair_agent,
        test_triage_agent,
        test_repair_agent,
        project_bug_hunt_agent,
        bugbot_agent,
        security_review_agent,
        archive_verify_agent,
        repair_adapter,
    )

    return await _run_compiled_main_graph(
        graph_factory,
        initial_state=initial_state,
        repo_root=repo_root,
        thread_id=thread_id,
        resume_scope=resume_scope,
        run_dir=run_dir,
        checkpoint_path=checkpoint_path,
    )


async def run_implement_then_verify_workflow(
    initial_state: GraphState,
    *,
    repo_root: Path,
    implementation_hop_agents: ImplementationHopAgents,
    impl_agent: ImplVerifyAgent,
    scenario_coverage_agent: ScenarioCoverageVerifyAgent,
    coverage_activation_agent: CoverageActivationAgent,
    apply_agent: ApplyAgent,
    scenario_test_repair_agent: ScenarioTestRepairAgent,
    group_error_agent: GroupErrorAgent,
    repair_order_agent: RepairOrderAgent,
    diagnostic_agent: DiagnosticAgent,
    static_triage_agent: StaticTriageAgent,
    typing_repair_agent: TypingRepairAgent,
    test_triage_agent: TestTriageAgent,
    test_repair_agent: TestRepairAgent,
    project_bug_hunt_agent: ProjectBugHuntAgent,
    bugbot_agent: BugBotAgent,
    security_review_agent: SecurityReviewAgent,
    archive_verify_agent: ArchiveVerifyAgent,
    repair_adapter: ABCAdapter,
    run_dir: Path | None = None,
    checkpoint_path: Path | None = None,
    thread_id: str,
    resume_scope: WorkflowResumeScope | None = None,
) -> GraphState:
    """``START → implementation → verify → END`` через ``build_main_graph``."""
    repo_root = resolve_opsx_repository_root(repo_root)
    initial_state = {**initial_state, **graph_repo_path_patch(initial_state, repo_root)}
    graph_factory = _build_graph_factory(
        impl_agent,
        scenario_coverage_agent,
        coverage_activation_agent,
        apply_agent,
        scenario_test_repair_agent,
        group_error_agent,
        repair_order_agent,
        diagnostic_agent,
        static_triage_agent,
        typing_repair_agent,
        test_triage_agent,
        test_repair_agent,
        project_bug_hunt_agent,
        bugbot_agent,
        security_review_agent,
        archive_verify_agent,
        repair_adapter,
        implementation_hop_agents=implementation_hop_agents,
    )

    return await _run_compiled_main_graph(
        graph_factory,
        initial_state=initial_state,
        repo_root=repo_root,
        thread_id=thread_id,
        resume_scope=resume_scope,
        run_dir=run_dir,
        checkpoint_path=checkpoint_path,
    )


__all__ = [
    "build_gate_repair_hop_agents",
    "build_implementation_hop_agents",
    "build_main_graph",
    "build_main_implementation_only_graph",
    "build_verify_hop_agents",
    "run_implement_then_verify_workflow",
    "run_workflow",
]
