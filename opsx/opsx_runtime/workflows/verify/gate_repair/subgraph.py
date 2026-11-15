"""Gate-repair subgraph: quality_gates → group_error → repair_order → diagnostic → dispatch_cycle."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.state import GraphState
from agents.verify.group_error.agent import GroupErrorAgent
from agents.verify.repair_order.agent import RepairOrderAgent
from agents.verify.diagnostic.agent import DiagnosticAgent
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.pytest_error_classifier.agent import PytestErrorClassifierAgent
from agents.verify.pytest_error_classifier.merge import merge_pytest_classifier_labels_patch
from agents.verify.environment_prep.agent import VerifyEnvironmentPrepAgent
from agents.verify.test_harness_repair.agent import TestHarnessRepairAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from workflows.verify.dispatch_cycle.subgraph import build_dispatch_cycle_graph
from workflows.verify.gate_repair.routing import (
    route_after_environment_prep,
    route_after_group_error,
    route_after_quality_gates_with_classifier,
)
from workflows.verify.routing_log import log_gate_repair_stop
from workflows.verify.gate_repair.runner import bind_quality_gates_agents, run_quality_gates_node
from workflows.verify.outer_batch import (
    verify_mark_batch_started_node,
    verify_reset_repair_batch_node,
)
from workflows.verify.outer_loop import (
    route_after_group_pass,
    route_after_quality_gates,  # noqa: F401 — fallback import for typing
    route_after_reset_repair_batch,
    verify_after_group_pass_node,
    verify_begin_outer_cycle_node,
    verify_mark_verify_ok_node,
)


async def _pytest_classifier_merge_node(state: GraphState) -> dict[str, Any]:
    """DET: зафиксировать merge marker (findings enrichment в group_error)."""
    return merge_pytest_classifier_labels_patch(state)


_REPAIR_HANDOFF_MISSING_FALLBACK = (
    "REPAIR_HANDOFF_MISSING: group_error completed without analysis_result; "
    "repair chain not authorized"
)


async def group_error_handoff_terminal_node(state: GraphState) -> dict[str, Any]:
    """DET: PAUSE без repair_order, когда group_error не отдал ``analysis_result``."""
    owner = state.get("owner_attention")
    owner_attention = (
        str(owner).strip()
        if isinstance(owner, str) and str(owner).strip()
        else _REPAIR_HANDOFF_MISSING_FALLBACK
    )
    patch: dict[str, Any] = {
        "verify_loop_status": "PAUSE",
        "verify_loop_terminal": True,
        "owner_attention": owner_attention,
    }
    log_gate_repair_stop(
        {**state, **patch},
        stop_kind="GROUP_ERROR_HANDOFF_MISSING",
        source="group_error_handoff_terminal_node",
    )
    return patch


def build_gate_repair_graph(
    group_error_agent: GroupErrorAgent,
    repair_order_agent: RepairOrderAgent,
    diagnostic_agent: DiagnosticAgent,
    static_triage_agent: StaticTriageAgent,
    typing_repair_agent: TypingRepairAgent,
    test_triage_agent: TestTriageAgent,
    test_repair_agent: TestRepairAgent,
    *,
    harness_repair_agent: TestHarnessRepairAgent | None = None,
    environment_prep_agent: VerifyEnvironmentPrepAgent | None = None,
    pytest_error_classifier_agent: PytestErrorClassifierAgent | None = None,
    repair_adapter=None,
    checkpointer=None,
):
    """Repair loop: ``quality_gates`` (pytest/pyright/mypy) → triage chain → dispatch group."""
    if harness_repair_agent is None and repair_adapter is not None:
        harness_repair_agent = TestHarnessRepairAgent(repair_adapter)
    if environment_prep_agent is None and repair_adapter is not None:
        environment_prep_agent = VerifyEnvironmentPrepAgent(repair_adapter)
    if pytest_error_classifier_agent is None and repair_adapter is not None:
        pytest_error_classifier_agent = PytestErrorClassifierAgent(repair_adapter)
    bind_quality_gates_agents(
        harness_repair_agent=harness_repair_agent,
        repair_adapter=repair_adapter,
    )
    dispatch_cycle = build_dispatch_cycle_graph(
        static_triage_agent,
        typing_repair_agent,
        test_triage_agent,
        test_repair_agent,
        harness_repair_agent=harness_repair_agent,
        repair_adapter=repair_adapter,
        checkpointer=checkpointer,
    )
    builder = StateGraph(GraphState)
    builder.add_node("verify_reset_repair_batch", verify_reset_repair_batch_node)
    builder.add_node("verify_begin_outer_cycle", verify_begin_outer_cycle_node)
    builder.add_node("quality_gates", run_quality_gates_node)
    if pytest_error_classifier_agent is not None:
        builder.add_node(
            "pytest_error_classifier",
            pytest_error_classifier_agent.run,
        )
        builder.add_node("pytest_classifier_merge", _pytest_classifier_merge_node)
    builder.add_node("verify_mark_verify_ok", verify_mark_verify_ok_node)
    builder.add_node("group_error", group_error_agent.run)
    builder.add_node("group_error_handoff_terminal", group_error_handoff_terminal_node)

    async def _environment_prep_node(state: GraphState) -> dict[str, Any]:
        if environment_prep_agent is None:
            return {"environment_prep_execution_status": "skipped_no_agent"}
        return await environment_prep_agent.run(state)

    builder.add_node("environment_prep", _environment_prep_node)
    builder.add_node("repair_order", repair_order_agent.run)
    builder.add_node("diagnostic", diagnostic_agent.run)
    builder.add_node("verify_mark_batch_started", verify_mark_batch_started_node)
    builder.add_node("dispatch_cycle", dispatch_cycle)
    builder.add_node("verify_after_group_pass", verify_after_group_pass_node)

    builder.add_edge(START, "verify_begin_outer_cycle")
    builder.add_edge("verify_begin_outer_cycle", "quality_gates")
    qg_route = (
        route_after_quality_gates_with_classifier
        if pytest_error_classifier_agent is not None
        else route_after_quality_gates
    )
    qg_targets: dict[str, str] = {
        "verify_mark_verify_ok": "verify_mark_verify_ok",
        "group_error": "group_error",
        "verify_reset_repair_batch": "verify_reset_repair_batch",
    }
    if pytest_error_classifier_agent is not None:
        qg_targets["pytest_error_classifier"] = "pytest_error_classifier"
    builder.add_conditional_edges("quality_gates", qg_route, qg_targets)
    if pytest_error_classifier_agent is not None:
        builder.add_edge("pytest_error_classifier", "pytest_classifier_merge")
        builder.add_edge("pytest_classifier_merge", "group_error")
    builder.add_conditional_edges(
        "verify_reset_repair_batch",
        route_after_reset_repair_batch,
        {
            END: END,
            "group_error": "group_error",
            "verify_begin_outer_cycle": "verify_begin_outer_cycle",
        },
    )
    builder.add_edge("verify_mark_verify_ok", END)
    builder.add_conditional_edges(
        "group_error",
        route_after_group_error,
        {
            "repair_order": "repair_order",
            "group_error_handoff_terminal": "group_error_handoff_terminal",
            "environment_prep": "environment_prep",
        },
    )
    builder.add_edge("group_error_handoff_terminal", END)
    builder.add_conditional_edges(
        "environment_prep",
        route_after_environment_prep,
        {
            "quality_gates": "quality_gates",
            "repair_order": "repair_order",
        },
    )
    builder.add_edge("repair_order", "diagnostic")
    builder.add_edge("diagnostic", "verify_mark_batch_started")
    builder.add_edge("verify_mark_batch_started", "dispatch_cycle")
    builder.add_edge("dispatch_cycle", "verify_after_group_pass")
    builder.add_conditional_edges(
        "verify_after_group_pass",
        route_after_group_pass,
        {
            "verify_begin_outer_cycle": "verify_begin_outer_cycle",
            "verify_mark_verify_ok": "verify_mark_verify_ok",
            "quality_gates": "quality_gates",
            "dispatch_cycle": "dispatch_cycle",
            END: END,
        },
    )
    return builder.compile(checkpointer=checkpointer)


build_gate_repair_subgraph = build_gate_repair_graph
