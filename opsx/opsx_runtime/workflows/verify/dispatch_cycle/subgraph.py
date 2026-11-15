"""LangGraph цикл dispatcher → unit triage → group repair/verify → dispatcher."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.adapters.abc_adapter import ABCAdapter
from agents.state import GraphState
from agents.verify.dispatch_unit.group_flow import (
    finalize_repair_unit_pass,
    group_repair_route_edge,
    prepare_causal_group,
    run_group_verify_node,
)
from agents.verify.dispatch_unit.group_triage import run_repair_unit_group_triage
from agents.verify.dispatcher.agent import dispatcher_prepare, dispatcher_route_edge
from agents.verify.static_triage.agent import StaticTriageAgent
from agents.verify.test_harness_repair.agent import TestHarnessRepairAgent
from agents.verify.test_repair.agent import TestRepairAgent
from agents.verify.test_duration.agent import TestDurationAgent
from agents.verify.test_duration.timeout_policy import repair_unit_is_duration_only
from agents.verify.test_interaction.agent import TestInteractionAgent
from agents.verify.test_triage.agent import TestTriageAgent
from agents.verify.typing_repair.agent import TypingRepairAgent
from workflows.verify.dispatch_cycle.commit_node import run_verify_finding_commit
from workflows.verify.dispatch_cycle.finalize import (
    static_finding_finalize,
    test_finding_finalize,
)
from workflows.verify.dispatch_cycle.logic_repair import run_verify_logic_bug_repair
from workflows.verify.dispatch_cycle.batch_recheck_handoff import run_batch_recheck_handoff
from workflows.verify.dispatch_cycle.pytest_execute_pre_repair import (
    pytest_execute_pre_repair_route,
    run_pytest_execute_pre_repair_node,
)
from workflows.verify.dispatch_cycle.pytest_personal_timeout import run_personal_timeout_probe
from workflows.verify.dispatch_cycle.pytest_single_repro import run_pytest_single_repro_node
from workflows.verify.dispatch_cycle.routing import (
    duration_route_edge,
    finding_post_repair_route_edge,
    personal_timeout_probe_route_edge,
    post_commit_route_edge,
    pytest_finding_triage_route_edge,
    pytest_single_repro_route_edge,
    static_triage_route_edge,
    interaction_post_probe_route_edge,
)
from workflows.verify.routing_log import log_dispatch_node_end, log_dispatch_node_start


def build_dispatch_cycle_graph(
    static_triage_agent: StaticTriageAgent,
    typing_repair_agent: TypingRepairAgent,
    test_triage_agent: TestTriageAgent,
    test_repair_agent: TestRepairAgent,
    *,
    harness_repair_agent: TestHarnessRepairAgent | None = None,
    repair_adapter: ABCAdapter | None = None,
    checkpointer=None,
):
    """Подграф dispatcher: repair unit (group triage → sequential groups) или legacy finding."""
    duration_agent = TestDurationAgent(test_triage_agent._adapter)
    interaction_agent = TestInteractionAgent(test_triage_agent._adapter)

    async def logic_bug_repair(state: GraphState) -> dict[str, Any]:
        if repair_adapter is None:
            return {
                "verify_finding_repair_outcome": {
                    "workflow": "bug_repair_fix_hop",
                    "status": "SKIPPED_NO_ADAPTER",
                },
            }
        return await run_verify_logic_bug_repair(state, repair_adapter)

    async def static_triage_node(state: GraphState) -> dict[str, Any]:
        started = log_dispatch_node_start(state, node="static_triage")
        patch = await static_triage_agent.run(state)
        log_dispatch_node_end(state, node="static_triage", started_at=started, patch=patch)
        return patch

    async def test_repair_node(state: GraphState) -> dict[str, Any]:
        started = log_dispatch_node_start(state, node="test_repair")
        patch = await test_repair_agent.run(state)
        log_dispatch_node_end(state, node="test_repair", started_at=started, patch=patch)
        return patch

    async def pytest_execute_pre_repair_node(state: GraphState) -> dict[str, Any]:
        started = log_dispatch_node_start(state, node="pytest_execute_pre_repair")
        patch = await run_pytest_execute_pre_repair_node(
            state,
            harness_repair_agent=harness_repair_agent,
            repair_adapter=repair_adapter,
        )
        log_dispatch_node_end(
            state,
            node="pytest_execute_pre_repair",
            started_at=started,
            patch=patch,
        )
        return patch

    async def test_harness_repair_node(state: GraphState) -> dict[str, Any]:
        started = log_dispatch_node_start(state, node="test_harness_repair")
        if harness_repair_agent is None:
            patch = {
                "test_harness_repair_result": {
                    "status": "BLOCKED",
                    "summary": "no harness agent",
                },
            }
        else:
            patch = await harness_repair_agent.run(state)
        log_dispatch_node_end(
            state, node="test_harness_repair", started_at=started, patch=patch,
        )
        return patch

    async def finding_commit_node(state: GraphState) -> dict[str, Any]:
        started = log_dispatch_node_start(state, node="finding_commit")
        patch = await run_verify_finding_commit(state, repair_adapter=repair_adapter)
        log_dispatch_node_end(state, node="finding_commit", started_at=started, patch=patch)
        return patch

    async def batch_recheck_handoff_node(state: GraphState) -> dict[str, Any]:
        started = log_dispatch_node_start(state, node="batch_recheck_handoff")
        patch = await run_batch_recheck_handoff(state)
        log_dispatch_node_end(
            state,
            node="batch_recheck_handoff",
            started_at=started,
            patch=patch,
        )
        return patch

    async def pytest_single_repro_node(state: GraphState) -> dict[str, Any]:
        started = log_dispatch_node_start(state, node="pytest_single_repro")
        patch = run_pytest_single_repro_node(state)
        log_dispatch_node_end(
            state,
            node="pytest_single_repro",
            started_at=started,
            patch=patch,
        )
        return patch

    async def test_interaction_node(state: GraphState) -> dict[str, Any]:
        started = log_dispatch_node_start(state, node="test_interaction")
        patch = await interaction_agent.run(state)
        log_dispatch_node_end(
            state,
            node="test_interaction",
            started_at=started,
            patch=patch,
        )
        return patch

    async def unit_group_triage_node(state: GraphState) -> dict[str, Any]:
        started = log_dispatch_node_start(state, node="unit_group_triage")
        mix_item = state.get("dispatcher_current_item")
        from agents.verify.dispatch_unit.queue_items import classify_unit_workflow_mix

        if isinstance(mix_item, dict) and repair_unit_is_duration_only(mix_item):
            patch = await duration_agent.triage_repair_unit(state)
        elif isinstance(mix_item, dict) and classify_unit_workflow_mix(mix_item) == "test":
            patch = await run_repair_unit_group_triage(
                state,
                adapter=test_triage_agent._adapter,
                agent_name="test_triage",
                agent_id_key="test_triage_agent_id",
                stream_log_key="test_triage_stream_log",
            )
        else:
            patch = await run_repair_unit_group_triage(
                state,
                adapter=static_triage_agent._adapter,
                agent_name="static_triage",
                agent_id_key="static_triage_agent_id",
                stream_log_key="static_triage_stream_log",
            )
        log_dispatch_node_end(
            state,
            node="unit_group_triage",
            started_at=started,
            patch=patch,
        )
        return patch

    builder = StateGraph(GraphState)
    builder.add_node("dispatcher", dispatcher_prepare)
    builder.add_node("unit_group_triage", unit_group_triage_node)
    builder.add_node("group_prepare", prepare_causal_group)
    builder.add_node("group_verify", run_group_verify_node)
    builder.add_node("unit_finalize", finalize_repair_unit_pass)
    builder.add_node("static_triage", static_triage_node)
    builder.add_node("test_triage", test_triage_agent.run)
    builder.add_node("test_duration", duration_agent.run)
    builder.add_node("pytest_personal_timeout", run_personal_timeout_probe)
    builder.add_node("pytest_single_repro", pytest_single_repro_node)
    builder.add_node("test_interaction", test_interaction_node)
    builder.add_node("pytest_execute_pre_repair", pytest_execute_pre_repair_node)
    builder.add_node("logic_bug_repair", logic_bug_repair)
    builder.add_node("typing_repair", typing_repair_agent.run)
    builder.add_node("test_repair", test_repair_node)
    builder.add_node("test_harness_repair", test_harness_repair_node)
    builder.add_node("finding_commit", finding_commit_node)
    builder.add_node("static_finding_finalize", static_finding_finalize)
    builder.add_node("test_finding_finalize", test_finding_finalize)
    builder.add_node("batch_recheck_handoff", batch_recheck_handoff_node)

    builder.add_edge(START, "dispatcher")
    builder.add_conditional_edges(
        "dispatcher",
        dispatcher_route_edge,
        {
            "unit_group_triage": "unit_group_triage",
            "group_prepare": "group_prepare",
            "group_verify": "group_verify",
            "unit_finalize": "unit_finalize",
            "static_triage": "static_triage",
            "test_triage": "test_triage",
            "test_duration": "test_duration",
            "pytest_single_repro": "pytest_single_repro",
            "dispatch_done": END,
        },
    )
    builder.add_conditional_edges(
        "pytest_single_repro",
        pytest_single_repro_route_edge,
        {
            "test_triage": "test_triage",
            "test_duration": "test_duration",
            "test_interaction": "test_interaction",
        },
    )
    builder.add_conditional_edges(
        "test_interaction",
        interaction_post_probe_route_edge,
        {
            "logic_bug_repair": "logic_bug_repair",
            "test_repair": "test_repair",
            "test_finding_finalize": "test_finding_finalize",
        },
    )
    builder.add_edge("unit_group_triage", "dispatcher")
    builder.add_conditional_edges(
        "group_prepare",
        group_repair_route_edge,
        {
            "logic_bug_repair": "logic_bug_repair",
            "typing_repair": "typing_repair",
            "test_repair": "test_repair",
            "test_harness_repair": "test_harness_repair",
            "pytest_personal_timeout": "pytest_personal_timeout",
            "group_verify": "group_verify",
        },
    )
    builder.add_edge("group_verify", "dispatcher")
    builder.add_edge("unit_finalize", "dispatcher")

    builder.add_conditional_edges(
        "static_triage",
        static_triage_route_edge,
        {
            "logic_bug_repair": "logic_bug_repair",
            "typing_repair": "typing_repair",
            "static_finding_finalize": "static_finding_finalize",
        },
    )
    builder.add_conditional_edges(
        "test_triage",
        pytest_finding_triage_route_edge,
        {
            "logic_bug_repair": "logic_bug_repair",
            "pytest_execute_pre_repair": "pytest_execute_pre_repair",
            "test_repair": "test_repair",
            "test_finding_finalize": "test_finding_finalize",
        },
    )
    builder.add_conditional_edges(
        "test_duration",
        duration_route_edge,
        {
            "logic_bug_repair": "logic_bug_repair",
            "test_repair": "test_repair",
            "pytest_personal_timeout": "pytest_personal_timeout",
            "test_finding_finalize": "test_finding_finalize",
        },
    )
    builder.add_conditional_edges(
        "pytest_personal_timeout",
        personal_timeout_probe_route_edge,
        {
            "test_duration": "test_duration",
            "test_repair": "test_repair",
            "test_finding_finalize": "test_finding_finalize",
            "group_verify": "group_verify",
        },
    )
    builder.add_conditional_edges(
        "pytest_execute_pre_repair",
        pytest_execute_pre_repair_route,
        {
            "test_repair": "test_repair",
            "test_finding_finalize": "test_finding_finalize",
        },
    )

    repair_route_map = {
        "finding_commit": "finding_commit",
        "batch_recheck_handoff": "batch_recheck_handoff",
        "static_finding_finalize": "static_finding_finalize",
        "test_finding_finalize": "test_finding_finalize",
        "group_verify": "group_verify",
    }
    for repair_node in ("logic_bug_repair", "typing_repair", "test_repair", "test_harness_repair"):
        builder.add_conditional_edges(
            repair_node,
            finding_post_repair_route_edge,
            repair_route_map,
        )

    builder.add_conditional_edges(
        "finding_commit",
        post_commit_route_edge,
        {
            "static_finding_finalize": "static_finding_finalize",
            "test_finding_finalize": "test_finding_finalize",
            "batch_recheck_handoff": "batch_recheck_handoff",
            "group_verify": "group_verify",
        },
    )
    builder.add_edge("batch_recheck_handoff", END)
    builder.add_edge("static_finding_finalize", "dispatcher")
    builder.add_edge("test_finding_finalize", "dispatcher")
    return builder.compile(checkpointer=checkpointer)


build_dispatch_cycle_subgraph = build_dispatch_cycle_graph
