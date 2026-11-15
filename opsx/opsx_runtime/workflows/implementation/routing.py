"""Куда идти после каждого шага implementation subgraph.

Схема: роутеры возвращают **семантический исход** (outcome key); сопоставление
исход → узел — только в ``graph.py`` (``ROUTE_AFTER_*_LANE``).

::

    START
      │
      ▼
    apply_design_lane          nested: design → design_review
      │
      ├─ PAUSE ────────────────────────► END subgraph (state сохраняется)
      ├─ FAILED ───────────────────────► raise в роутере / hop
      ├─ неизвестно ───────────────────► raise (на месте в роутере)
      └─ scenario_test_authoring ────► implementation_slice_list
                                           │
                                           ├─ lane_complete_finalize ─► implementation_finalize
                                           ├─ return_to_design ───────► apply_design_lane
                                           ├─ PAUSE ──────────────────► END subgraph
                                           ├─ FAIL ───────────────────► raise
                                           └─ иначе ──────────────────► raise в роутере
      implementation_finalize ─────────────► END (parent: authoring → materialize → verify)

Plan SoT: ``opsx-super-implement.mjs``; отдельного узла ``implementation_route`` в графе нет.

``implementation_arbiter``: implement-hop ``ESCALATE_ARBITER`` → parent ``implementation_arbiter``
(макс. ``ARBITER_MAX_CALLS`` на run) → design или slice lane по directive; ``BLOCKED`` — только Owner PAUSE.

Id узлов — строковые литералы только в ``graph.py``; outcome keys — в роутерах и path_map.
"""

from __future__ import annotations

from typing import NoReturn

from langgraph.graph import END

from agents.state import GraphState
from workflows.implementation.design_pipeline.routing import design_lane_forward_plan_in_state
from workflows.implementation.routing_contract import (
    RUN_STATUS_APPLY_COMPLETE,
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_COMPLETE,
    implementation_lane_error_detail,
    implementation_lane_should_execute,
)

def implementation_code_entry_node_id(graph: object) -> str | None:
    """Id узла code lane в compiled graph (nested ``apply_code_lane`` или legacy execute)."""
    nodes = getattr(graph, "nodes", None)
    if isinstance(nodes, dict):
        if "implementation_one_slice" in nodes:
            return "implementation_one_slice"
        if "apply_code_lane" in nodes:
            return "apply_code_lane"
        if "implementation_execute_hop" in nodes:
            return "implementation_execute_hop"
    return None


# True в state: в этом заходе был только plan, SDK не вызывался (см. execute-узел).
PLAN_APPLIED_WITHOUT_HOP = "implementation_plan_applied_without_hop"

# Семантические исходы conditional edges (ключи path_map в ``graph.py``).
OUTCOME_ENTER_CODE_LANE = "enter_code_lane"
OUTCOME_ENTER_SCENARIO_AUTHORING = "scenario_test_authoring"
OUTCOME_CONTINUE_EXECUTE = "continue_execute"
OUTCOME_LANE_COMPLETE_FINALIZE = "lane_complete_finalize"
OUTCOME_RETURN_TO_DESIGN = "return_to_design"
OUTCOME_NEXT_SLICE = "next_slice"
OUTCOME_ENTER_ONE_SLICE = "enter_one_slice"
OUTCOME_ENTER_ARBITER = "enter_arbiter"

ARBITER_MAX_CALLS = 3


def _route_pending_arbiter_or_continue(state: GraphState) -> str | None:
    """``implementation_arbiter_pending`` → enter arbiter или budget raise."""
    if not state.get("implementation_arbiter_pending"):
        return None
    calls = int(state.get("implementation_arbiter_calls") or 0)
    if calls >= ARBITER_MAX_CALLS:
        raise ImplementationLaneTerminalError(
            f"ARBITER_BUDGET_EXHAUSTED: arbiter already invoked {calls} times "
            f"(max {ARBITER_MAX_CALLS})",
        )
    return OUTCOME_ENTER_ARBITER


class ImplementationLaneTerminalError(RuntimeError):
    """FAILED и неожиданный стоп implementation lane; PAUSE — штатный ``END`` subgraph."""


def _raise_lane_terminal(state: GraphState) -> NoReturn:
    """Упасть сразу в роутере — без отдельного узла «error»."""
    status = str(state.get("implementation_status") or "")
    code = str(state.get("implementation_recovery_code") or "")
    stage = str(state.get("implementation_stage") or "")
    err = implementation_lane_error_detail(state)
    slice_id = str(state.get("implementation_slice_id") or "")
    raise ImplementationLaneTerminalError(
        f"implementation lane stop: status={status!r} stage={stage!r} "
        f"slice_id={slice_id!r} recovery_code={code!r} error={err!r}",
    )


def _raise_if_failed_else_end_on_paused(state: GraphState) -> str | None:
    """``FAILED`` и ``PAUSED`` — ``raise``. Иначе ``None`` (роутер продолжает).

    Тихий ``END`` на паузе проглатывает ошибку: CLI видит успешный граф.
    """
    status = str(state.get("implementation_status") or "")
    if status in (RUN_STATUS_FAILED, RUN_STATUS_PAUSED):
        _raise_lane_terminal(state)
    return None


def lane_apply_complete(state: GraphState) -> bool:
    """План или runtime говорят: split-APPLY lane закончен → finalize."""
    if str(state.get("implementation_status") or "") == RUN_STATUS_APPLY_COMPLETE:
        return True
    if str(state.get("implementation_stage") or "") == STAGE_APPLY_COMPLETE:
        return True
    plan = state.get("implementation_plan")
    if not isinstance(plan, dict):
        return False
    if str(plan.get("status") or "") == RUN_STATUS_APPLY_COMPLETE:
        return True
    return str(plan.get("next_stage") or "") == STAGE_APPLY_COMPLETE


def _design_ready_for_main_loop(state: GraphState) -> bool:
    status = str(state.get("implementation_status") or "")
    if design_lane_forward_plan_in_state(state):
        return True
    if state.get("apply_design_lane_complete"):
        return True
    return status == "IN_PROGRESS" and not str(state.get("implementation_stage") or "").strip()


def route_after_slice_list(state: GraphState) -> str:
    """После узла списка слайсов — один слайс или стоп."""
    terminal = _raise_if_failed_else_end_on_paused(state)
    if terminal is not None:
        return terminal
    slices = state.get("implementation_slices")
    if not isinstance(slices, list) or not slices:
        _raise_lane_terminal(state)
    return OUTCOME_ENTER_ONE_SLICE


def route_after_one_slice(state: GraphState) -> str:
    """После subgraph одного слайса: следующий слайс, дизайн, finalize или END."""
    arbiter = _route_pending_arbiter_or_continue(state)
    if arbiter is not None:
        return arbiter
    if state.get("implementation_force_design_hop"):
        if not state.get("implementation_arbiter_directed_design_hop"):
            count = int(state.get("implementation_design_redo_count") or 0)
            if count > 1:
                _raise_lane_terminal(state)
        terminal = _raise_if_failed_else_end_on_paused(state)
        if terminal is not None:
            return terminal
        return OUTCOME_RETURN_TO_DESIGN
    terminal = _raise_if_failed_else_end_on_paused(state)
    if terminal is not None:
        return terminal
    slices = state.get("implementation_slices")
    idx = int(state.get("implementation_slice_index") or 0)
    if isinstance(slices, list) and idx < len(slices):
        return OUTCOME_NEXT_SLICE
    if lane_apply_complete(state) or state.get("implementation_lane_complete"):
        return OUTCOME_LANE_COMPLETE_FINALIZE
    if lane_apply_complete(state):
        return OUTCOME_LANE_COMPLETE_FINALIZE
    _raise_lane_terminal(state)


def route_after_apply_design_lane(state: GraphState) -> str:
    """После design lane: scenario authoring, затем список слайсов."""
    arbiter = _route_pending_arbiter_or_continue(state)
    if arbiter is not None:
        return arbiter
    terminal = _raise_if_failed_else_end_on_paused(state)
    if terminal is not None:
        return terminal
    status = str(state.get("implementation_status") or "")
    if status == RUN_STATUS_APPLY_COMPLETE:
        return OUTCOME_ENTER_SCENARIO_AUTHORING
    if _design_ready_for_main_loop(state):
        return OUTCOME_ENTER_SCENARIO_AUTHORING
    _raise_lane_terminal(state)


def route_after_scenario_authoring(state: GraphState) -> str:
    """После authoring: slice list при COMPLETE (узел иначе raise)."""
    terminal = _raise_if_failed_else_end_on_paused(state)
    if terminal is not None:
        return terminal
    if str(state.get("scenario_authoring_status") or "") == "COMPLETE":
        return OUTCOME_ENTER_CODE_LANE
    _raise_lane_terminal(state)


def route_after_apply_code_lane(state: GraphState) -> str:
    """После nested code lane: семантический исход → узел задаёт path_map в ``graph.py``."""
    if state.get("implementation_force_design_hop"):
        return OUTCOME_RETURN_TO_DESIGN
    if lane_apply_complete(state):
        return OUTCOME_LANE_COMPLETE_FINALIZE
    terminal = _raise_if_failed_else_end_on_paused(state)
    if terminal is not None:
        return terminal
    _raise_lane_terminal(state)


def route_after_execute_hop(state: GraphState) -> str:
    """После execute-узла (plan ± SDK): исход → см. path_map в ``code_pipeline/graph.py``."""
    if state.get("implementation_force_design_hop"):
        return OUTCOME_RETURN_TO_DESIGN
    if lane_apply_complete(state):
        return OUTCOME_LANE_COMPLETE_FINALIZE

    status = str(state.get("implementation_status") or "")
    plan_only = bool(state.get(PLAN_APPLIED_WITHOUT_HOP))
    if plan_only:
        if implementation_lane_should_execute(state):
            return OUTCOME_CONTINUE_EXECUTE
        terminal = _raise_if_failed_else_end_on_paused(state)
        if terminal is not None:
            return terminal
        _raise_lane_terminal(state)

    terminal = _raise_if_failed_else_end_on_paused(state)
    if terminal is not None:
        return terminal
    if status == RUN_STATUS_RUNNING:
        return OUTCOME_CONTINUE_EXECUTE
    _raise_lane_terminal(state)


# --- тестовые обёртки (симулируют ветку plan-only / post-SDK) ---


def route_after_plan_only(state: GraphState) -> str:
    return route_after_execute_hop({**state, PLAN_APPLIED_WITHOUT_HOP: True})


def route_after_sdk_hop(state: GraphState) -> str:
    return route_after_execute_hop({**state, PLAN_APPLIED_WITHOUT_HOP: False})


def route_main_after_implementation(state: GraphState) -> str:
    """Parent graph: implementation subgraph → verify или END."""
    terminal = _raise_if_failed_else_end_on_paused(state)
    if terminal is not None:
        return terminal
    status = str(state.get("implementation_status") or "")
    if status == RUN_STATUS_APPLY_COMPLETE:
        return "verify"
    _raise_lane_terminal(state)
