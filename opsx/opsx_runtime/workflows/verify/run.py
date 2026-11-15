"""Запуск verify LangGraph через главный parent graph ``workflows.main_graph.run_workflow``."""

from __future__ import annotations

from pathlib import Path

from agents.adapters.abc_adapter import ABCAdapter
from agents.state import GraphState
from agents.implementation.apply.agent import ApplyAgent
from agents.verify.impl_verify.agent import ImplVerifyAgent
from agents.verify.coverage_activation.agent import CoverageActivationAgent
from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent
from agents.verify.group_error.agent import GroupErrorAgent
from agents.verify.repair_order.agent import RepairOrderAgent
from agents.verify.diagnostic.agent import DiagnosticAgent
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from agents.verify.project_bug_hunt.agent import ProjectBugHuntAgent
from agents.verify.archive_verify.agent import ArchiveVerifyAgent
from agents.verify.security_review.agent import SecurityReviewAgent
from agents.verify.bugbot.agent import BugBotAgent
from agents.repair.scenario_test_repair.agent import ScenarioTestRepairAgent
from workflows.checkpoint_resume import (
    WorkflowResumeScope,
    durable_langgraph_checkpoint_enabled,
)


async def run_verify_workflow(
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
    thread_id: str | None = None,
    resume_scope: WorkflowResumeScope | None = None,
) -> GraphState:
    """Выполнить verify-граф: PHASE_IMPL → gate_repair subgraph (через ``build_main_graph``)."""
    from workflows.main import run_workflow

    if resume_scope is None and thread_id is None and durable_langgraph_checkpoint_enabled():
        change_id = str(initial_state.get("change_id") or "")
        if change_id:
            resume_scope = WorkflowResumeScope(kind="verify_impl", change_id=change_id)
    if thread_id is None:
        thread_id = ""

    return await run_workflow(
        initial_state,
        repo_root=repo_root,
        impl_agent=impl_agent,
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
        run_dir=run_dir,
        checkpoint_path=checkpoint_path,
        thread_id=thread_id,
        resume_scope=resume_scope,
    )
