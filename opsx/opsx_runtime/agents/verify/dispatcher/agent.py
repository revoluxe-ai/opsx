"""Узлы Dispatcher: repair unit pass и legacy finding pass."""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.dispatch_unit.queue_items import is_repair_unit_item
from agents.verify.dispatcher.queue import ensure_dispatcher_pass_initialized
from agents.verify.dispatcher.routing import classify_dispatch_route


def _queue_slot(state: GraphState) -> tuple[list[dict[str, Any]], int, dict[str, Any] | None]:
    queue = list(state.get("dispatcher_pass_queue") or [])
    index = int(state.get("dispatcher_pass_index") or 0)
    slot = queue[index] if index < len(queue) else None
    return queue, index, slot if isinstance(slot, dict) else None


def _in_causal_group_cycle(state: GraphState) -> bool:
    current = state.get("dispatcher_current_item")
    return (
        bool(state.get("dispatcher_unit_triage_complete"))
        and isinstance(current, dict)
        and current.get("item_kind") == "causal_group"
    )


async def dispatcher_prepare(state: GraphState) -> dict[str, Any]:
    """Выбрать repair unit / finding и маршрут следующего hop."""
    patch: dict[str, Any] = dict(ensure_dispatcher_pass_initialized(state))
    merged: GraphState = {**state, **patch}

    if merged.get("dispatcher_pass_complete"):
        return {
            **patch,
            "dispatcher_current_item": None,
            "dispatcher_route": None,
            "dispatcher_cycle_status": "COMPLETE",
        }

    queue, index, slot = _queue_slot(merged)
    if index >= len(queue):
        return {
            **patch,
            "dispatcher_pass_complete": True,
            "dispatcher_current_item": None,
            "dispatcher_route": None,
            "dispatcher_cycle_status": "COMPLETE",
        }

    base: dict[str, Any] = {**patch, "dispatcher_cycle_status": "IN_PROGRESS"}

    if _in_causal_group_cycle(merged):
        base["dispatcher_current_item"] = merged.get("dispatcher_current_item")
    else:
        base["dispatcher_current_item"] = slot

    unit_slot = slot if is_repair_unit_item(slot) else None
    if unit_slot is not None:
        if not merged.get("dispatcher_unit_triage_complete"):
            return {**base, "dispatcher_route": "unit_triage"}
        if str(merged.get("dispatcher_group_phase") or "") == "verify":
            return {**base, "dispatcher_route": "group_verify"}
        if merged.get("verify_finding_fix") is not None and _in_causal_group_cycle(merged):
            return {**base, "dispatcher_route": "group_verify"}
        groups = list(merged.get("dispatch_causal_groups") or [])
        g_idx = int(merged.get("dispatcher_group_index") or 0)
        if g_idx >= len(groups):
            return {**base, "dispatcher_route": "unit_finalize"}
        return {**base, "dispatcher_route": "group_repair"}

    route = classify_dispatch_route(slot or {})
    return {**base, "dispatcher_route": route}


def dispatcher_route_edge(state: GraphState) -> str:
    """Conditional edge из dispatcher."""
    if state.get("dispatcher_pass_complete"):
        return "dispatch_done"
    route = state.get("dispatcher_route")
    if route == "unit_triage":
        return "unit_group_triage"
    if route == "group_repair":
        return "group_prepare"
    if route == "group_verify":
        return "group_verify"
    if route == "unit_finalize":
        return "unit_finalize"
    if route == "static":
        return "static_triage"
    if route == "test":
        item = state.get("dispatcher_current_item")
        observation = str(item.get("observation") or "") if isinstance(item, dict) else ""
        from agents.verify.dispatch_unit.repro_cohort import pytest_node_id

        node_id = pytest_node_id(observation) or ""
        if node_id and node_id != "UNKNOWN_NODE":
            return "pytest_single_repro"
        from agents.verify.test_duration.timeout_policy import is_pytest_duration_text

        if is_pytest_duration_text(observation):
            return "test_duration"
        return "test_triage"
    return "dispatch_done"


async def dispatcher_record_outcome(
    state: GraphState,
    *,
    workflow_kind: str,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    """Сохранить outcome текущего pass item и сдвинуть индекс очереди."""
    item = state.get("dispatcher_current_item")
    outcomes = list(state.get("dispatcher_outcomes") or [])
    key = None
    if isinstance(item, dict):
        key = item.get("repair_unit_key") or item.get("finding_id")
    outcomes.append(
        {
            "finding_id": key,
            "workflow_kind": workflow_kind,
            "item": item,
            "outcome": outcome,
        },
    )
    index = int(state.get("dispatcher_pass_index") or 0) + 1
    queue = list(state.get("dispatcher_pass_queue") or [])
    complete = index >= len(queue)
    return {
        "dispatcher_outcomes": outcomes,
        "dispatcher_pass_index": index,
        "dispatcher_pass_complete": complete,
        "dispatcher_current_item": None,
        "dispatcher_route": None,
    }
