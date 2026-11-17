"""Прямой subgraph одного слайса: scaffold → red → implement → green."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.implementation.apply_green.agent import ApplyGreenAgent
from agents.implementation.apply_implement.agent import ApplyImplementAgent
from agents.implementation.apply_red.agent import ApplyRedAgent
from agents.implementation.apply_scaffold.agent import ApplyScaffoldAgent
from agents.state import GraphState
from workflows.artifact_paths import useful_stage_receipt_path
from workflows.implementation.routing import ImplementationLaneTerminalError
from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
    RUN_STATUS_RUNNING,
)
from workflows.implementation.slice_pipeline.stage_hop import run_slice_stage_hop


@dataclass(frozen=True)
class SliceLaneAgents:
    """Агенты одного слайса (scaffold … green)."""

    apply_scaffold: ApplyScaffoldAgent
    apply_red: ApplyRedAgent
    apply_implement: ApplyImplementAgent
    apply_green: ApplyGreenAgent


def slice_lane_agents_from_hop_agents(hop_agents: Any) -> SliceLaneAgents:
    """Собрать ``SliceLaneAgents`` из полного ``ImplementationHopAgents``."""
    return SliceLaneAgents(
        apply_scaffold=hop_agents.apply_scaffold,
        apply_red=hop_agents.apply_red,
        apply_implement=hop_agents.apply_implement,
        apply_green=hop_agents.apply_green,
    )


def code_lane_agents_from_hop_agents(hop_agents: Any) -> SliceLaneAgents:
    """Алиас для совместимости с ``code_pipeline.graph``."""
    return slice_lane_agents_from_hop_agents(hop_agents)


async def _scaffold_node(state: GraphState, *, agents: SliceLaneAgents) -> dict[str, Any]:
    return await run_slice_stage_hop(state, agent=agents.apply_scaffold, stage=STAGE_APPLY_SCAFFOLD)


async def _red_node(state: GraphState, *, agents: SliceLaneAgents) -> dict[str, Any]:
    return await run_slice_stage_hop(state, agent=agents.apply_red, stage=STAGE_APPLY_RED)


async def _implement_node(state: GraphState, *, agents: SliceLaneAgents) -> dict[str, Any]:
    return await run_slice_stage_hop(state, agent=agents.apply_implement, stage=STAGE_APPLY_IMPLEMENT)


async def _green_node(state: GraphState, *, agents: SliceLaneAgents) -> dict[str, Any]:
    return await run_slice_stage_hop(state, agent=agents.apply_green, stage=STAGE_APPLY_GREEN)


def _should_retry_red_oracle_gap(state: GraphState) -> bool:
    """Первый отказ materialize RED (oracle gap / publish missing): один повтор hop.

    ``stage_hop`` выставляет ``implementation_red_oracle_retry_count``; directive
    в checkpoint может отсутствовать — достаточно ``retry_count == 1``.
    Сброс счётчика при valid PRE/SKIP — в ``stage_hop``; наличие устаревшего
    ``RED.json`` с диска не отменяет разрешённый ``retry_red``.
    """
    if str(state.get("implementation_last_hop") or "").strip() != STAGE_APPLY_RED:
        return False
    if str(state.get("implementation_status") or "") != RUN_STATUS_RUNNING:
        return False
    if int(state.get("implementation_red_oracle_retry_count") or 0) != 1:
        return False
    target = str(state.get("implementation_arbiter_target_stage") or "").strip()
    if target and target != STAGE_APPLY_RED:
        return False
    return True


def _should_retry_green_verify(state: GraphState) -> bool:
    """Первый отказ replay frozen commands: один повтор APPLY_GREEN.

    ``stage_hop`` выставляет ``implementation_green_verify_retry_count``.
    Успешный GREEN или SKIP сбрасывает счётчик, иначе успешный hop снова
    попал бы в ``retry_green``.
    """
    if str(state.get("implementation_last_hop") or "").strip() != STAGE_APPLY_GREEN:
        return False
    if str(state.get("implementation_status") or "") != RUN_STATUS_RUNNING:
        return False
    if int(state.get("implementation_green_verify_retry_count") or 0) != 1:
        return False
    target = str(state.get("implementation_arbiter_target_stage") or "").strip()
    if target and target != STAGE_APPLY_GREEN:
        return False
    return True


def route_after_slice_stage(state: GraphState) -> str:
    """PAUSED — END subgraph; FAILED — raise; иначе следующий stage."""
    if state.get("implementation_arbiter_pending"):
        return END
    if state.get("implementation_force_design_hop"):
        return END
    last_hop = str(state.get("implementation_last_hop") or "").strip()
    if _should_retry_red_oracle_gap(state):
        return "retry_red"
    if _should_retry_green_verify(state):
        return "retry_green"
    from workflows.implementation.routing import (
        _raise_if_failed_else_end_on_paused,
    )

    terminal = _raise_if_failed_else_end_on_paused(state)
    if terminal is not None:
        return terminal
    if last_hop == STAGE_APPLY_RED:
        change_id = str(state.get("change_id") or "").strip()
        slice_id = str(state.get("implementation_slice_id") or "").strip()
        repo_raw = str(state.get("repository_path") or "").strip()
        if change_id and slice_id and repo_raw:
            red_useful = useful_stage_receipt_path(
                Path(repo_raw),
                change_id,
                STAGE_APPLY_RED,
                slice_id,
            )
            if not red_useful.is_file():
                if _should_retry_red_oracle_gap(state):
                    return "retry_red"
                raise ImplementationLaneTerminalError(
                    "RECEIPT_PUBLISH_MISSING: "
                    f"useful RED.json absent for {slice_id} after RED hop",
                )
    return "continue"


_SLICE_START_NODE_BY_STAGE: dict[str, str] = {
    STAGE_APPLY_SCAFFOLD: "slice_apply_scaffold",
    STAGE_APPLY_RED: "slice_apply_red",
    STAGE_APPLY_IMPLEMENT: "slice_apply_implement",
    STAGE_APPLY_GREEN: "slice_apply_green",
}


def route_slice_lane_start(state: GraphState) -> str:
    """START: при arbiter directive — сразу на целевой stage."""
    directive = str(state.get("implementation_arbiter_directive") or "").strip()
    target = str(state.get("implementation_arbiter_target_stage") or "").strip()
    if directive and target in _SLICE_START_NODE_BY_STAGE:
        return _SLICE_START_NODE_BY_STAGE[target]
    return "slice_apply_scaffold"


def build_apply_slice_lane_graph(
    hop_agents: SliceLaneAgents,
    *,
    checkpointer=None,
):
    """Скомпилировать subgraph одного слайса (четыре hop-узла подряд)."""
    builder = StateGraph(GraphState)
    _ = builder.add_node(
        "slice_apply_scaffold",
        partial(_scaffold_node, agents=hop_agents),
    )
    _ = builder.add_node("slice_apply_red", partial(_red_node, agents=hop_agents))
    _ = builder.add_node(
        "slice_apply_implement",
        partial(_implement_node, agents=hop_agents),
    )
    _ = builder.add_node("slice_apply_green", partial(_green_node, agents=hop_agents))

    _ = builder.add_conditional_edges(
        START,
        route_slice_lane_start,
        {
            "slice_apply_scaffold": "slice_apply_scaffold",
            "slice_apply_red": "slice_apply_red",
            "slice_apply_implement": "slice_apply_implement",
            "slice_apply_green": "slice_apply_green",
        },
    )
    _ = builder.add_conditional_edges(
        "slice_apply_scaffold",
        route_after_slice_stage,
        {"continue": "slice_apply_red", END: END},
    )
    _ = builder.add_conditional_edges(
        "slice_apply_red",
        route_after_slice_stage,
        {
            "continue": "slice_apply_implement",
            "retry_red": "slice_apply_red",
            END: END,
        },
    )
    _ = builder.add_conditional_edges(
        "slice_apply_implement",
        route_after_slice_stage,
        {"continue": "slice_apply_green", END: END},
    )
    _ = builder.add_conditional_edges(
        "slice_apply_green",
        route_after_slice_stage,
        {"continue": END, "retry_green": "slice_apply_green", END: END},
    )
    return builder.compile(checkpointer=checkpointer)


def build_apply_code_lane_graph(
    hop_agents: Any,
    *,
    checkpointer=None,
):
    """Точка входа legacy name: один слайс, без plan-loop."""
    agents = (
        hop_agents
        if isinstance(hop_agents, SliceLaneAgents)
        else slice_lane_agents_from_hop_agents(hop_agents)
    )
    return build_apply_slice_lane_graph(agents, checkpointer=checkpointer)
