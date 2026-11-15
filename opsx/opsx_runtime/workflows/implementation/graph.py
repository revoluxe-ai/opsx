"""LangGraph implementation lane: design subgraph → code subgraph → finalize."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import ABCAdapter
from agents.implementation.apply_design.agent import ApplyDesignAgent
from agents.implementation.apply_design_review.agent import ApplyDesignReviewAgent
from agents.implementation.apply_green.agent import ApplyGreenAgent
from agents.implementation.apply_implement.agent import ApplyImplementAgent
from agents.implementation.apply_red.agent import ApplyRedAgent
from agents.implementation.apply_scaffold.agent import ApplyScaffoldAgent
from agents.implementation.implementation_arbiter.agent import ImplementationArbiterAgent
from agents.planning.scenario_coverage_plan.agent import ScenarioCoveragePlanAgent
from agents.implementation.scenario_test_author.agent import ScenarioTestAuthorAgent
from agents.state import GraphState
from langgraph.graph import END, START, StateGraph
from workflows.implementation import stage_dispatch
from workflows.implementation.slice_list import implementation_slice_list_node
from workflows.implementation.slice_pipeline.graph import (
    build_apply_slice_lane_graph,
    slice_lane_agents_from_hop_agents,
)
from workflows.implementation.design_pipeline.graph import (
    build_apply_design_lane_graph,
    design_lane_agents_from_hop_agents,
)
from workflows.implementation.ledger import read_live_execution_authorized
from workflows.implementation.arbiter_hop import (
    OUTCOME_TARGET_DESIGN,
    OUTCOME_TARGET_SLICE,
    arbiter_node_factory,
    route_after_arbiter,
)
from workflows.implementation.routing import (
    OUTCOME_ENTER_ARBITER,
    OUTCOME_ENTER_CODE_LANE,
    OUTCOME_ENTER_ONE_SLICE,
    OUTCOME_LANE_COMPLETE_FINALIZE,
    OUTCOME_NEXT_SLICE,
    OUTCOME_RETURN_TO_DESIGN,
    ImplementationLaneTerminalError,
    OUTCOME_ENTER_SCENARIO_AUTHORING,
    route_after_apply_design_lane,
    route_after_one_slice,
    route_after_scenario_authoring,
    route_after_slice_list,
)
from workflows.implementation.routing_contract import STAGE_APPLY_SCAFFOLD
from workflows.implementation.routing_contract import (
    RUN_STATUS_APPLY_COMPLETE,
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_COMPLETE,
)
from workflows.repo_paths import resolve_opsx_repository_root
from workflows.scenario_parent.nodes import authoring_node_factory

_SHARED_LANE_PARENT_KEYS: tuple[str, ...] = (
    "implementation_force_design_hop",
    "artifact_bundle",
    "implementation_machine_gate_errors",
    "implementation_plan",
    "implementation_plan_reject_code",
    "implementation_primitive_command",
    "error",
    "implementation_recovery_code",
    "implementation_slice_id",
    "implementation_stage",
    "implementation_status",
    "live_execution_authorized",
    "implementation_arbiter_pending",
    "implementation_arbiter_request",
    "implementation_arbiter_calls",
    "implementation_arbiter_directive",
    "implementation_arbiter_target_stage",
    "implementation_arbiter_answer",
    "implementation_arbiter_directed_design_hop",
    "implementation_arbiter_agent_id",
    "implementation_arbiter_session_run_uuid",
)

_SLICE_LANE_PARENT_KEYS: tuple[str, ...] = _SHARED_LANE_PARENT_KEYS + (
    "implementation_last_hop",
    "implementation_recovery_contract",
    "implementation_receipt_skip",
    "implementation_unchanged_dispatch_count",
    "implementation_lane_complete",
    "implementation_plan_applied_without_hop",
    "implementation_slices",
    "implementation_slice_index",
    "implementation_design_redo_count",
    "implementation_receipt_gate",
    "implementation_slice_receipt_validation",
)

_DESIGN_LANE_PARENT_KEYS: tuple[str, ...] = ("apply_design_lane_complete",) + _SHARED_LANE_PARENT_KEYS


async def _invoke_apply_design_lane_subgraph(
    design_lane: Any,
    state: GraphState,
) -> dict[str, Any]:
    """Проброс design-lane полей в parent checkpoint (R-001 nested merge)."""
    out = await design_lane.ainvoke(state)
    patch: dict[str, Any] = {}
    for key in _DESIGN_LANE_PARENT_KEYS:
        if key in out:
            patch[key] = out[key]
    return patch


async def _invoke_apply_one_slice_subgraph(
    slice_lane: Any,
    state: GraphState,
) -> dict[str, Any]:
    """Проброс slice-lane полей, включая ``error``, и продвижение индекса после зелёного."""
    out = await slice_lane.ainvoke(state)
    patch: dict[str, Any] = {}
    for key in _SLICE_LANE_PARENT_KEYS:
        if key in out:
            patch[key] = out[key]
    for key, value in out.items():
        if key.startswith("apply_") and key.endswith("_status"):
            patch[key] = value
    if out.get("implementation_arbiter_pending"):
        return patch
    if out.get("implementation_force_design_hop"):
        return patch
    status = str(out.get("implementation_status") or state.get("implementation_status") or "")
    if status in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED):
        return patch
    last_hop = str(out.get("implementation_last_hop") or "")
    if last_hop != "APPLY_GREEN":
        return patch
    idx = int(state.get("implementation_slice_index") or 0)
    slices = state.get("implementation_slices")
    if not isinstance(slices, list):
        return patch
    if idx + 1 < len(slices):
        next_row = slices[idx + 1]
        patch["implementation_slice_index"] = idx + 1
        patch["implementation_slice_id"] = (
            str(next_row.get("slice_id")) if isinstance(next_row, dict) else str(next_row)
        )
        patch["implementation_stage"] = STAGE_APPLY_SCAFFOLD
        patch["implementation_status"] = RUN_STATUS_RUNNING
    else:
        patch["implementation_slice_index"] = idx + 1
        patch["implementation_status"] = RUN_STATUS_APPLY_COMPLETE
        patch["implementation_stage"] = STAGE_APPLY_COMPLETE
        patch["implementation_lane_complete"] = True
    return patch


@dataclass(frozen=True)
class ImplementationHopAgents:
    """Split-APPLY runtime agents (без monolithic ``/opsx-apply``)."""

    apply_design: ApplyDesignAgent
    apply_design_review: ApplyDesignReviewAgent
    apply_scaffold: ApplyScaffoldAgent
    apply_red: ApplyRedAgent
    apply_implement: ApplyImplementAgent
    apply_green: ApplyGreenAgent
    implementation_arbiter: ImplementationArbiterAgent
    scenario_coverage_plan: ScenarioCoveragePlanAgent
    scenario_test_author: ScenarioTestAuthorAgent


def build_implementation_hop_agents(
    *,
    adapter: ABCAdapter | None = None,
    apply_adapter: ABCAdapter | None = None,
) -> ImplementationHopAgents:
    """Собрать apply_design … apply_green с общим mutating adapter."""
    resolved_adapter = adapter if adapter is not None else apply_adapter
    if resolved_adapter is None:
        raise TypeError("build_implementation_hop_agents() requires adapter= or apply_adapter=")
    from check_workflow_receipt import CheckWorkflowReceipt

    noop = CheckWorkflowReceipt.none()
    return ImplementationHopAgents(
        apply_design=ApplyDesignAgent(resolved_adapter, receipt=noop),
        apply_design_review=ApplyDesignReviewAgent(resolved_adapter, receipt=noop),
        apply_scaffold=ApplyScaffoldAgent(resolved_adapter, receipt=noop),
        apply_red=ApplyRedAgent(resolved_adapter, receipt=noop),
        apply_implement=ApplyImplementAgent(resolved_adapter, receipt=noop),
        apply_green=ApplyGreenAgent(resolved_adapter, receipt=noop),
        implementation_arbiter=ImplementationArbiterAgent(resolved_adapter, receipt=noop),
        scenario_coverage_plan=ScenarioCoveragePlanAgent(resolved_adapter),
        scenario_test_author=ScenarioTestAuthorAgent(resolved_adapter),
    )


async def implementation_finalize_node(state: GraphState) -> dict[str, Any]:
    """Закрытие lane: записать ``RUN.json`` receipt и handoff в verify."""
    change_id = str(state.get("change_id") or "").strip()
    repo_root = resolve_opsx_repository_root(Path(str(state["repository_path"])))
    if change_id and not bool(state.get("implementation_receipt_skip")):
        live_flag = state.get("live_execution_authorized")
        live = read_live_execution_authorized(
            change_id,
            repo_root=repo_root,
            state_flag=live_flag if isinstance(live_flag, bool) else None,
        )
        _ = stage_dispatch.advance_super_implement_receipt(
            change_id,
            repo_root=repo_root,
            live_execution_authorized=live,
        )
    return {
        "implementation_status": RUN_STATUS_APPLY_COMPLETE,
        "implementation_lane_complete": True,
        "implementation_stage": STAGE_APPLY_COMPLETE,
    }


def build_implementation_graph(
    agents: ImplementationHopAgents,
    *,
    checkpointer=None,
):
    """Собрать implementation subgraph (см. схему в ``routing.py``)."""
    design_lane = build_apply_design_lane_graph(
        design_lane_agents_from_hop_agents(agents),
        checkpointer=checkpointer,
    )
    slice_lane = build_apply_slice_lane_graph(
        slice_lane_agents_from_hop_agents(agents),
        checkpointer=checkpointer,
    )

    builder = StateGraph(GraphState)
    _ = builder.add_node(
        "apply_design_lane",
        partial(_invoke_apply_design_lane_subgraph, design_lane),
    )
    _ = builder.add_node(
        "scenario_test_authoring",
        authoring_node_factory(agents.scenario_test_author),
    )
    _ = builder.add_node("implementation_slice_list", implementation_slice_list_node)
    _ = builder.add_node(
        "implementation_one_slice",
        partial(_invoke_apply_one_slice_subgraph, slice_lane),
    )
    _ = builder.add_node("implementation_finalize", implementation_finalize_node)
    _ = builder.add_node(
        "implementation_arbiter",
        arbiter_node_factory(agents.implementation_arbiter),
    )

    _ = builder.add_edge(START, "apply_design_lane")
    _ = builder.add_conditional_edges(
        "apply_design_lane",
        route_after_apply_design_lane,
        {
            OUTCOME_ENTER_SCENARIO_AUTHORING: "scenario_test_authoring",
            OUTCOME_ENTER_ARBITER: "implementation_arbiter",
            END: END,
        },
    )
    _ = builder.add_conditional_edges(
        "scenario_test_authoring",
        route_after_scenario_authoring,
        {
            OUTCOME_ENTER_CODE_LANE: "implementation_slice_list",
            END: END,
        },
    )
    _ = builder.add_conditional_edges(
        "implementation_slice_list",
        route_after_slice_list,
        {
            OUTCOME_ENTER_ONE_SLICE: "implementation_one_slice",
            END: END,
        },
    )
    _ = builder.add_conditional_edges(
        "implementation_one_slice",
        route_after_one_slice,
        {
            OUTCOME_LANE_COMPLETE_FINALIZE: "implementation_finalize",
            OUTCOME_NEXT_SLICE: "implementation_one_slice",
            OUTCOME_RETURN_TO_DESIGN: "apply_design_lane",
            OUTCOME_ENTER_ARBITER: "implementation_arbiter",
            END: END,
        },
    )
    _ = builder.add_conditional_edges(
        "implementation_arbiter",
        route_after_arbiter,
        {
            OUTCOME_TARGET_DESIGN: "apply_design_lane",
            OUTCOME_TARGET_SLICE: "implementation_one_slice",
            END: END,
        },
    )
    _ = builder.add_edge("implementation_finalize", END)
    return builder.compile(checkpointer=checkpointer)
