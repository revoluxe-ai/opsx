"""LangGraph verify workflow: ``impl_verify`` → gate_repair → review chain."""

from __future__ import annotations

from agents.adapters.abc_adapter import ABCAdapter
from agents.implementation.apply.agent import ApplyAgent
from agents.verify.bugbot.agent import BugBotAgent
from agents.verify.diagnostic.agent import DiagnosticAgent
from agents.verify.group_error.agent import GroupErrorAgent
from agents.verify.impl_verify.agent import ImplVerifyAgent
from agents.verify.coverage_activation.agent import CoverageActivationAgent
from agents.verify.scenario_coverage_verify.agent import ScenarioCoverageVerifyAgent
from agents.verify.project_bug_hunt.agent import ProjectBugHuntAgent
from agents.verify.repair_order.agent import RepairOrderAgent
from agents.verify.archive_verify.agent import ArchiveVerifyAgent
from agents.verify.security_review.agent import SecurityReviewAgent
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from agents.repair.scenario_test_repair.agent import ScenarioTestRepairAgent
from workflows.verify.graph import build_verify_graph


def build_impl_verify_graph(
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
    checkpointer=None,
):
    """START → ``impl_verify`` → scenario coverage → coverage_activation → gate_repair → review."""
    return build_verify_graph(
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
