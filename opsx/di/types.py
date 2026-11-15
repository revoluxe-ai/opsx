"""Типы связки verify-impl для composition root DI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.adapters.abc_adapter import ABCAdapter
    from agents.adapters.cursor_bridge_runtime import CursorBridgeRuntime
    from agents.verify.bugbot.agent import BugBotAgent
    from agents.verify.project_bug_hunt.agent import ProjectBugHuntAgent
    from agents.verify.security_review.agent import SecurityReviewAgent
    from workflows.checkpoint_resume import WorkflowResumeScope
    from workflows.implementation.implementation_dispatch_ledger import (
        ImplementationDispatchLedger,
    )
    from workflows.implementation.adapter_run_reconcile import AdapterRunReconcileReport


@dataclass(frozen=True, slots=True)
class VerifySessionPaths:
    """Файловые артефакты одной сессии verify-impl."""

    session_id: str
    verify_dir: Path
    stream_log: Path
    group_error_stream_log: Path
    repair_order_stream_log: Path
    diagnostic_stream_log: Path


@dataclass(frozen=True, slots=True)
class VerifyImplAdapters:
    """Адаптеры Cursor SDK для review и repair/apply hop."""

    review_adapter: ABCAdapter
    apply_adapter: ABCAdapter


@dataclass(slots=True)
class VerifyHopAgentBundle:
    """Агенты subgraph verify (impl verify, coverage, apply, archive)."""

    impl_agent: Any
    scenario_coverage_agent: Any
    coverage_activation_agent: Any
    apply_agent: Any
    scenario_test_repair_agent: Any
    archive_verify_agent: Any


@dataclass(slots=True)
class GateRepairHopAgentBundle:
    """Агенты цепочки group_error → repair_order → diagnostic."""

    group_error_agent: Any
    repair_order_agent: Any
    diagnostic_agent: Any
    static_triage_agent: Any
    typing_repair_agent: Any
    test_triage_agent: Any
    test_repair_agent: Any
    harness_repair_agent: Any
    pytest_classifier_agent: Any


@dataclass(slots=True)
class ReviewAgentBundle:
    """Read-only review-агенты verify (bug hunt, bugbot, security)."""

    project_bug_hunt_agent: ProjectBugHuntAgent
    bugbot_agent: BugBotAgent
    security_review_agent: SecurityReviewAgent


@dataclass(slots=True)
class LedgerReconcileOutcome:
    """Результат reconcile при старте bridge и dispatch ledger."""

    ledger: ImplementationDispatchLedger
    reconcile_report: AdapterRunReconcileReport
    session_id: str


@dataclass(slots=True)
class VerifyImplExecuteContext:
    """Собранные зависимости ``run_verify_impl.execute`` до вызова workflow.

    Bridge lifecycle остаётся снаружи (``MainContainer.open_bridge`` / ``providers.Resource``).
    """

    bridge: CursorBridgeRuntime
    adapters: VerifyImplAdapters
    verify_hop_agents: VerifyHopAgentBundle
    gate_repair_agents: GateRepairHopAgentBundle
    review_agents: ReviewAgentBundle
    implementation_hop_agents: Any
    session_paths: VerifySessionPaths
    initial_state: dict[str, Any]
    resume_scope: WorkflowResumeScope | None
    ledger_outcome: LedgerReconcileOutcome
