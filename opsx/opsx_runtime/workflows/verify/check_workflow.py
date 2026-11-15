"""Общий шаблон verify-check workflow: discovery → repair → (discovery|END|retry).

После repair — условная маршрутизация по ``CheckRepairHopOutcome`` (не unconditional
re-discovery при BLOCKED / VERIFY_PAUSED).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

if TYPE_CHECKING:
    from check_workflow_receipt import CheckWorkflowReceipt

from agents.abc_agent import ABCAgent
from agents.adapters.abc_adapter import ABCAdapter
from agents.state import GraphState
from workflows.verify.bugbot.bugbot_pipeline.check_repair_graph import (
    ReviewMapper,
    make_bug_team_repair_hop,
)
from workflows.verify.check_repair_outcome import (
    route_after_check_repair,
    state_key_prefix_for_check,
)
from workflows.verify.cycle_checks import CheckName
from workflows.verify.green_loop import (
    is_project_bug_workflow_green,
    is_security_workflow_green,
    patch_establish_verify_check_circle,
    verify_pass_had_code_changes,
)

_CHECK_NAME_BY_PREFIX: dict[str, CheckName] = {
    "project_bug": CheckName.PROJECT_BUG,
    "bugbot": CheckName.BUGBOT,
    "security": CheckName.SECURITY,
}


async def _run_discovery_agent(state: GraphState, *, agent: ABCAgent) -> dict[str, Any]:
    """Discovery-hop (hunt / bugbot / security review)."""
    return await agent.run(state)


def is_bugbot_workflow_green(state: GraphState) -> bool:
    """Bugbot check-repair зелёный: нет BLOCKED/FINDINGS в review."""
    review = state.get("review")
    if not isinstance(review, dict):
        return True
    status = review.get("status")
    if status == "BLOCKED":
        return False
    if status == "FINDINGS":
        findings = review.get("findings")
        return not isinstance(findings, list) or not findings
    return True


def _resolve_is_green(name_prefix: str) -> Callable[[GraphState], bool]:
    if name_prefix == "project_bug":
        return is_project_bug_workflow_green
    if name_prefix == "bugbot":
        return is_bugbot_workflow_green
    if name_prefix == "security":
        return is_security_workflow_green
    msg = f"unknown check workflow name_prefix: {name_prefix!r}"
    raise ValueError(msg)


def _repair_hop_kwargs(name_prefix: str) -> dict[str, Any]:
    if name_prefix == "security":
        return {
            "repair_run_segment": "security-repair",
            "mission_prefix": "verify-sec",
            "state_key_prefix": "security",
        }
    return {}


def build_check_workflow_graph(
    discovery_agent: ABCAgent,
    repair_adapter: ABCAdapter,
    *,
    review_mapper: ReviewMapper | None,
    name_prefix: str,
    checkpointer=None,
    receipt: CheckWorkflowReceipt | None = None,
):
    """Собрать compiled subgraph одного verify-check (project bug / bugbot / security)."""
    is_green = _resolve_is_green(name_prefix)
    check_name = _CHECK_NAME_BY_PREFIX.get(name_prefix)
    if check_name is None:
        msg = f"unsupported check workflow name_prefix: {name_prefix!r}"
        raise ValueError(msg)

    iteration_key = f"{name_prefix}_check_loop_pass"
    begin_node = f"{name_prefix}_begin"
    discovery_node = f"{name_prefix}_discovery"
    repair_node = f"{name_prefix}_check_repair"

    repair_hop = make_bug_team_repair_hop(
        repair_adapter,
        review_mapper=review_mapper,
        checkpointer=checkpointer,
        **_repair_hop_kwargs(name_prefix),
    )

    async def begin_check_workflow_node(_state: GraphState) -> dict[str, Any]:
        return {
            "verify_workflow_code_changed": False,
            iteration_key: 0,
        }

    async def discovery_node_handler(state: GraphState) -> dict[str, Any]:
        patch = await _run_discovery_agent(state, agent=discovery_agent)
        merged: GraphState = {**state, **patch}
        if is_green(merged) and not verify_pass_had_code_changes(merged):
            if receipt is not None:
                patch = {**patch, **receipt.establish_patch(merged)}
            else:
                patch = {**patch, **patch_establish_verify_check_circle(merged, check_name)}
        return patch

    async def repair_node_handler(state: GraphState) -> dict[str, Any]:
        """Bug-team repair; счётчик repair-циклов только для диагностики."""
        patch = await repair_hop(state)
        count = int(state.get(iteration_key) or 0) + 1
        return {**patch, iteration_key: count}

    builder = StateGraph(GraphState)
    builder.add_node(begin_node, begin_check_workflow_node)
    builder.add_node(discovery_node, discovery_node_handler)
    builder.add_node(repair_node, repair_node_handler)

    def route_after_begin(state: GraphState) -> str:
        if receipt is not None:
            from check_workflow_receipt import CheckWorkflowReceiptStatus

            if receipt.check(state) is CheckWorkflowReceiptStatus.MATCH:
                return END
        return discovery_node

    _ = builder.add_edge(START, begin_node)
    if receipt is not None:
        _ = builder.add_conditional_edges(
            begin_node,
            route_after_begin,
            {END: END, discovery_node: discovery_node},
        )
    else:
        _ = builder.add_edge(begin_node, discovery_node)
    _ = builder.add_conditional_edges(
        discovery_node,
        lambda state: repair_node if not is_green(state) else END,
        {repair_node: repair_node, END: END},
    )

    def _route_after_repair(state: GraphState) -> str:
        target = route_after_check_repair(state, name_prefix)
        if target == "END":
            return END
        if target == repair_node:
            return repair_node
        if not is_green(state):
            sk = state_key_prefix_for_check(name_prefix)
            wf = str(state.get(f"{sk}_check_repair_workflow_outcome") or "")
            if state.get("verify_workflow_paused") is True or wf == "VERIFY_PAUSED":
                return END
            if wf == "SUCCESS":
                return END
        return discovery_node

    _ = builder.add_conditional_edges(
        repair_node,
        _route_after_repair,
        {END: END, repair_node: repair_node, discovery_node: discovery_node},
    )
    return builder.compile(checkpointer=checkpointer)


# Совместимость: старое имя factory subgraph.
build_check_workflow = build_check_workflow_graph
