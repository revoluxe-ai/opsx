"""Изолированный subgraph code lane: plan + SDK для scaffold/red/implement/green.

Executable stages в ``routing_contract.EXECUTABLE_STAGES`` также включают design;
в этом subgraph исполняются только ``CODE_LANE_STAGES`` (scaffold, red, implement,
green). Design остаётся в ``design_pipeline.graph``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import ABCAdapter, ActiveCursorAgentRunError
from agents.implementation.apply_green.agent import ApplyGreenAgent
from agents.implementation.apply_implement.agent import ApplyImplementAgent
from agents.implementation.apply_red.agent import ApplyRedAgent
from agents.implementation.apply_scaffold.agent import ApplyScaffoldAgent
from agents.implementation.hop_lane_status import graph_dispatch_answer
from agents.state import GraphState
from agents.verify.impl_verify.preflight import load_change_artifact_bundle
from langgraph.graph import END, START, StateGraph
from workflows.implementation import plan_hop
from workflows.implementation.design_pipeline.routing import DESIGN_LANE_STAGES
from workflows.implementation.hop_stagnation import stagnation_patch_after_hop
from workflows.implementation.implementation_dispatch_ledger import (
    ImplementationDispatchLedger,
    dispatch_ledger_path,
)
from workflows.implementation.routing import (
    OUTCOME_RETURN_TO_DESIGN,
    PLAN_APPLIED_WITHOUT_HOP,
    lane_apply_complete,
    route_after_execute_hop,
)
from workflows.implementation.routing_contract import (
    EXECUTABLE_STAGES,
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
    STAGE_RECOVERY_PAUSED,
    format_current_implementation_procedure,
    implementation_lane_should_execute,
)
from workflows.implementation.adapter_hop_pause import (
    patch_implementation_adapter_outcome_unknown,
    patch_implementation_execute_terminal_pause,
)
from workflows.implementation.adapter_run_reconcile import (
    Pause,
    AdapterRunReconcileReport,
    decide_mutating_dispatch_allowed,
    get_stored_reconcile_report,
    resolve_dispatch_exclude_run_id,
)
from workflows.repo_paths import resolve_opsx_repository_root

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodeLaneAgents:
    """Только scaffold / red / implement / green hops."""

    apply_scaffold: ApplyScaffoldAgent
    apply_red: ApplyRedAgent
    apply_implement: ApplyImplementAgent
    apply_green: ApplyGreenAgent


def code_lane_agents_from_hop_agents(hop_agents: Any) -> CodeLaneAgents:
    """Собрать ``CodeLaneAgents`` из полного ``ImplementationHopAgents``."""
    return CodeLaneAgents(
        apply_scaffold=hop_agents.apply_scaffold,
        apply_red=hop_agents.apply_red,
        apply_implement=hop_agents.apply_implement,
        apply_green=hop_agents.apply_green,
    )


def _bridge_runtime_from_agents(agents: CodeLaneAgents) -> Any | None:
    """Borrow verify-impl bridge from any mutating hop adapter (composition root)."""
    for hop_agent in (
        agents.apply_implement,
        agents.apply_red,
        agents.apply_green,
        agents.apply_scaffold,
    ):
        adapter = getattr(hop_agent, "_adapter", None)
        bridge = getattr(adapter, "bridge_runtime", None) if adapter is not None else None
        if bridge is not None:
            return bridge
    return None


def _reconcile_for_execute_state(
    state: GraphState,
    *,
    change_id: str,
    repo_root: Path,
) -> AdapterRunReconcileReport:
    """Предзагруженный reconcile parent verify-impl (``run_uuid``), иначе empty rows."""
    session = str(state.get("run_uuid") or "").strip()
    if session:
        stored = get_stored_reconcile_report(session)
        if stored is not None:
            return stored
    return AdapterRunReconcileReport(change_id=change_id, index_db=repo_root, rows=[])


def dispatch_gate_recovery_code(reason: str) -> str:
    """Typed pause code из ``decide_mutating_dispatch_allowed`` reason."""
    text = str(reason or "").strip()
    if text.startswith("dispatch_ledger_active_hop"):
        return "DISPATCH_LEDGER_ACTIVE_HOP"
    if text.startswith("sdk_index_get_run_unavailable"):
        return "SDK_BRIDGE_TIMEOUT"
    if text.startswith("sdk_reconcile_block") or text.startswith("sdk_index_get_run_block"):
        return "SDK_ACTIVE_RUN_CONFLICT"
    return "SDK_ACTIVE_RUN_CONFLICT"


def _parse_run_id_from_dispatch_reason(reason: str) -> str | None:
    for token in str(reason or "").split():
        if token.startswith("run_id="):
            return token.split("=", 1)[1].strip() or None
        if token.startswith("run_ids="):
            ids = token.split("=", 1)[1].strip()
            return ids.split(",")[0].strip() or None
    return None


def main_lane_bootstrap_patch(state: GraphState) -> dict[str, Any]:
    """Первый заход в main apply loop после design lane (раньше ``implementation_begin_node``).

    ``RUN.json`` не закрывает lane — только fresh plan в route.
    """
    change_id = str(state.get("change_id") or "").strip()
    if not change_id:
        raise ValueError("change_id is required for implementation lane")
    repo_root = resolve_opsx_repository_root(Path(str(state["repository_path"])))
    bundle = load_change_artifact_bundle(change_id, repo_root=repo_root)
    return {
        "artifact_bundle": bundle,
        "implementation_status": "IN_PROGRESS",
        "implementation_stage": None,
        "implementation_slice_id": None,
        "implementation_recovery_code": None,
        "implementation_plan_reject_code": None,
        "implementation_unchanged_dispatch_count": 0,
        "implementation_lane_complete": False,
        "implementation_receipt_skip": False,
    }


def _needs_main_lane_bootstrap(state: GraphState) -> bool:
    return "implementation_unchanged_dispatch_count" not in state


async def run_main_plan_hop(state: GraphState) -> dict[str, Any]:
    """Вызов ``opsx-super-implement.mjs plan`` и patch state (не узел графа)."""
    patch: dict[str, Any] = {}
    if _needs_main_lane_bootstrap(state):
        patch.update(main_lane_bootstrap_patch(state))
    merged: GraphState = {**state, **patch}
    change_id = str(merged.get("change_id") or "").strip()
    repo_root = plan_hop.resolved_repo_root(merged)
    live = plan_hop.read_live_flag(merged, change_id, repo_root)
    step, reused = plan_hop.resolve_plan_step(
        "main",
        change_id,
        state=merged,
        repo_root=repo_root,
        live_execution_authorized=live,
        plan_payload_override=plan_hop.plan_override_from_state(merged),
    )
    prev_stage = str(merged.get("implementation_stage") or "")
    prev_slice = str(merged.get("implementation_slice_id") or "")
    patch.update(
        plan_hop.build_main_lane_plan_patch(
            step,
            prev_stage=prev_stage,
            prev_slice=prev_slice,
            plan_reused=reused,
        ),
    )
    return patch


# Сохраняем имя для тестов и checkpoint, которые патчат plan-hop.
implementation_route_node = run_main_plan_hop


def _resolve_agent_for_stage(
    stage: str,
    agents: CodeLaneAgents,
) -> Any | None:
    if stage == STAGE_APPLY_SCAFFOLD:
        return agents.apply_scaffold
    if stage == STAGE_APPLY_RED:
        return agents.apply_red
    if stage == STAGE_APPLY_IMPLEMENT:
        return agents.apply_implement
    if stage == STAGE_APPLY_GREEN:
        return agents.apply_green
    return None


async def implementation_execute_hop_node(
    state: GraphState,
    *,
    agents: CodeLaneAgents,
) -> dict[str, Any]:
    """Один визит узла ``implementation_execute_hop``: **plan** → при необходимости **SDK hop**."""
    if lane_apply_complete(state) and not implementation_lane_should_execute(state):
        return {PLAN_APPLIED_WITHOUT_HOP: True}
    # 1) Plan (Node SoT)
    plan_patch = await run_main_plan_hop(state)
    working: GraphState = {**state, **plan_patch}

    if working.get("implementation_force_design_hop"):
        return {**plan_patch, PLAN_APPLIED_WITHOUT_HOP: True}

    # 2) Только plan — без SDK (флаг для route_after_execute_hop)
    if lane_apply_complete(working) or not implementation_lane_should_execute(working):
        return {**plan_patch, PLAN_APPLIED_WITHOUT_HOP: True}

    # 3) Один mutating hop по ``implementation_stage`` (только code lane stages)
    change_id = str(working.get("change_id") or "").strip()
    stage = str(working.get("implementation_stage") or "")
    repo_root = resolve_opsx_repository_root(Path(str(working["repository_path"])))
    if stage in DESIGN_LANE_STAGES:
        return {
            "implementation_status": RUN_STATUS_FAILED,
            "implementation_recovery_code": "DESIGN_LANE_REQUIRED",
            "error": (
                f"stage {stage!r} belongs to apply_design_lane subgraph; "
                "code lane must not run it"
            ),
        }
    if stage == STAGE_RECOVERY_PAUSED:
        return {
            "implementation_status": RUN_STATUS_PAUSED,
            "implementation_recovery_code": str(
                working.get("implementation_recovery_code") or "RECOVERY_PAUSED",
            ),
            "implementation_stage": stage,
            "implementation_slice_id": working.get("implementation_slice_id"),
            "implementation_machine_gate_errors": working.get("implementation_machine_gate_errors"),
            "implementation_recovery_contract": working.get("implementation_recovery_contract"),
        }
    if stage not in EXECUTABLE_STAGES:
        return {
            "implementation_status": RUN_STATUS_FAILED,
            "implementation_recovery_code": "UNKNOWN_STAGE",
            "error": f"unsupported implementation stage: {stage!r}",
        }

    agent = _resolve_agent_for_stage(stage, agents)
    if agent is None:
        return patch_implementation_execute_terminal_pause(
            stage=stage,
            slice_id=str(working.get("implementation_slice_id") or "") or None,
            recovery_code="STAGE_NOT_WIRED",
            message=f"no runtime agent for stage {stage!r}",
        )
    slice_id = str(working.get("implementation_slice_id") or "") or None
    ledger_path = dispatch_ledger_path(change_id, repo_root=repo_root)
    ledger = ImplementationDispatchLedger(ledger_path)
    reconcile = _reconcile_for_execute_state(
        working,
        change_id=change_id,
        repo_root=repo_root,
    )
    bridge = _bridge_runtime_from_agents(agents)
    exclude_run_id = resolve_dispatch_exclude_run_id(
        stage=stage,
        run_id=str(working.get("run_id") or "") or None,
        implementation_stage=str(working.get("implementation_stage") or "") or None,
        implementation_status=str(working.get("implementation_status") or "") or None,
        implementation_recovery_code=str(working.get("implementation_recovery_code") or "")
        or None,
    )
    try:
        dispatch_gate = decide_mutating_dispatch_allowed(
            change_id=change_id,
            stage=stage,
            repo_root=repo_root,
            reconcile=reconcile,
            ledger=ledger,
            exclude_run_id=exclude_run_id,
            bridge=bridge,
        )
    except Exception as exc:
        return patch_implementation_adapter_outcome_unknown(
            stage=stage,
            slice_id=slice_id,
            agent_id=str(state.get("agent_id") or ""),
            run_id=None,
            exc=exc,
        )
    if isinstance(dispatch_gate, Pause):
        reason = str(dispatch_gate.reason or "")
        return patch_implementation_execute_terminal_pause(
            stage=stage,
            slice_id=slice_id,
            recovery_code=dispatch_gate_recovery_code(reason),
            message=reason,
            agent_id=str(state.get("agent_id") or "") or None,
            run_id=_parse_run_id_from_dispatch_reason(reason),
        )
    hop_state: GraphState = {
        **working,
        "mode": stage.lower(),
        "answer": graph_dispatch_answer(working, slice_id),
    }
    hop_log_run_id = str(working.get("run_id") or "").strip() or ""
    procedure = format_current_implementation_procedure(stage, slice_id)
    _LOG.info(
        "implementation hop start change_id=%s procedure=%s run_id=%s",
        change_id,
        procedure,
        hop_log_run_id,
    )
    hop_end_run_id = hop_log_run_id
    try:
        result = await agent.run(hop_state)
        hop_end_run_id = str(result.get("run_id") or hop_log_run_id or "").strip() or hop_log_run_id
    except ActiveCursorAgentRunError as exc:
        if exc.run_id:
            hop_end_run_id = str(exc.run_id).strip()
        pause_patch = patch_implementation_adapter_outcome_unknown(
            stage=stage,
            slice_id=slice_id,
            agent_id=exc.agent_id,
            run_id=exc.run_id,
            exc=exc,
        )
        if exc.run_id:
            ledger.upsert_active_hop(
                change_id=change_id,
                slice_id=slice_id,
                stage=stage,
                agent_id=exc.agent_id,
                run_id=str(exc.run_id),
                sdk_status="PAUSED",
                primary_error=str(pause_patch.get("implementation_recovery_code") or ""),
            )
        return pause_patch
    except Exception as exc:
        return patch_implementation_adapter_outcome_unknown(
            stage=stage,
            slice_id=slice_id,
            agent_id=str(state.get("agent_id") or ""),
            run_id=None,
            exc=exc,
        )
    finally:
        _LOG.info(
            "implementation hop end change_id=%s procedure=%s run_id=%s",
            change_id,
            procedure,
            hop_end_run_id,
        )
    merged: dict[str, Any] = {
        **plan_patch,
        **result,
        "implementation_last_hop": stage,
    }
    hop_run = str(merged.get("run_id") or "").strip()
    hop_agent = str(merged.get("agent_id") or "").strip() or None
    if hop_run:
        ledger.upsert_active_hop(
            change_id=change_id,
            slice_id=slice_id,
            stage=stage,
            agent_id=hop_agent,
            run_id=hop_run,
            sdk_status="FINISHED",
        )
    hop_status = str(merged.get(f"{stage.lower()}_status") or "")
    merged.update(
        stagnation_patch_after_hop(
            {**working, **result},
            executed_stage=stage,
            executed_slice=str(slice_id) if slice_id else None,
            hop_status=hop_status,
            hop_result=result,
        ),
    )
    merged[PLAN_APPLIED_WITHOUT_HOP] = False
    return merged


def build_apply_code_lane_graph(
    hop_agents: CodeLaneAgents,
    *,
    checkpointer=None,
):
    """Compiled subgraph scaffold/red/implement/green (plan+SDK loop внутри lane)."""
    builder = StateGraph(GraphState)
    _ = builder.add_node(
        "implementation_execute_hop",
        partial(implementation_execute_hop_node, agents=hop_agents),
    )
    _ = builder.add_edge(START, "implementation_execute_hop")
    _ = builder.add_conditional_edges(
        "implementation_execute_hop",
        route_after_execute_hop,
        {
            "continue_execute": "implementation_execute_hop",
            "lane_complete_finalize": END,
            OUTCOME_RETURN_TO_DESIGN: END,
            END: END,
        },
    )
    return builder.compile(checkpointer=checkpointer)
