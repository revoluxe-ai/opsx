"""Изолированный subgraph: APPLY_DESIGN → APPLY_DESIGN_REVIEW.

Следующая стадия выбирается по каноническому design и review на диске.
Node ``plan`` этот выбор не делает.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.implementation.apply_design.agent import ApplyDesignAgent
from agents.implementation.apply_design_review.agent import ApplyDesignReviewAgent
from agents.adapters.abc_adapter import ActiveCursorAgentRunError
from agents.state import GraphState
from agents.verify.impl_verify.preflight import load_change_artifact_bundle
from workflows.implementation.design_pipeline.routing import (
    DESIGN_LANE_EXECUTE_NODE,
    DESIGN_LANE_ROUTE_NODE,
    DESIGN_LANE_STAGES,
    design_lane_forward_plan_in_state,
    route_after_design_lane_execute,
    route_after_design_lane_route,
)
from agents.implementation.hop_lane_status import graph_dispatch_answer
from workflows.implementation.receipt_loader import (
    is_apply_design_review_fresh,
    load_apply_design,
)
from workflows.implementation.routing_contract import (
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_DESIGN,
    STAGE_APPLY_DESIGN_REVIEW,
    format_current_implementation_procedure,
)
from workflows.implementation import plan_hop
from workflows.implementation.adapter_hop_pause import (
    patch_implementation_adapter_outcome_unknown,
)
from workflows.repo_paths import resolve_opsx_repository_root

_LOG = logging.getLogger(__name__)

_APPLY_DESIGN_READY = "APPLY_DESIGN_READY"


@dataclass(frozen=True)
class DesignLaneAgents:
    """Только design + design-review hops."""

    apply_design: ApplyDesignAgent
    apply_design_review: ApplyDesignReviewAgent


def _dry_run_enabled() -> bool:
    return os.environ.get("OPSX_IMPLEMENTATION_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _design_lane_idempotent_skip(change_id: str, *, repo_root: Path) -> bool:
    """True когда design+review готовы и машинный контракт APPLY-DESIGN валиден."""
    design = load_apply_design(change_id, repo_root=repo_root)
    if design is None or str(design.get("status") or "") != _APPLY_DESIGN_READY:
        return False
    if not is_apply_design_review_fresh(change_id, repo_root=repo_root):
        return False
    from workflows.implementation.design_machine_validation import (
        design_lane_reuse_ok_after_dependent_snapshot,
        evaluate_apply_design_dependent_snapshot,
        validate_apply_design_machine,
    )

    gate = validate_apply_design_machine(change_id, repo_root=repo_root)
    if not gate.ok:
        return False
    snapshot = evaluate_apply_design_dependent_snapshot(change_id, repo_root=repo_root)
    return design_lane_reuse_ok_after_dependent_snapshot(
        snapshot,
        machine_gate_ok=gate.ok,
    )


def _design_lane_bootstrap_patch(state: GraphState) -> dict[str, Any]:
    """Первый заход в design lane: artifact bundle и сброс ``apply_design_lane_complete``."""
    change_id = str(state.get("change_id") or "").strip()
    if not change_id:
        raise ValueError("change_id is required for apply design lane")
    repo_root = plan_hop.resolved_repo_root(state)
    bundle = load_change_artifact_bundle(change_id, repo_root=repo_root)
    patch: dict[str, Any] = {
        "artifact_bundle": bundle,
        "apply_design_lane_complete": False,
    }
    if _design_lane_idempotent_skip(change_id, repo_root=repo_root):
        patch["apply_design_lane_complete"] = True
        patch["implementation_status"] = "IN_PROGRESS"
        patch["implementation_stage"] = None
    return patch


def _design_redo_route_patch(state: GraphState) -> dict[str, Any]:
    """Повтор design после hop, который признал design неверным.

    Snapshot может ещё быть свежим: агент всё равно запускается, причина
    берётся из ``implementation_machine_gate_errors`` / recovery contract.
    """
    attempt = int(state.get("implementation_design_attempt") or 0)
    if attempt >= 3:
        return {
            **plan_hop.continue_after_design_review(
                {},
                design_attempt=attempt,
            ),
        }
    errors = state.get("implementation_machine_gate_errors")
    if not isinstance(errors, list) or not errors:
        contract = state.get("implementation_recovery_contract")
        message = "design must be redone before slices continue"
        code = str(state.get("implementation_recovery_code") or "DESIGN_REDO")
        if isinstance(contract, dict):
            message = str(contract.get("next_action") or contract.get("code") or message)
        errors = [{"code": code, "message": message}]
    return {
        "apply_design_lane_complete": False,
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_stage": STAGE_APPLY_DESIGN,
        "implementation_slice_id": None,
        "implementation_primitive_command": "/opsx-apply-design",
        "implementation_machine_gate_errors": errors,
        "implementation_design_attempt": attempt + 1 if attempt else 1,
    }


async def apply_design_lane_route_node(state: GraphState) -> dict[str, Any]:
    """Plan hop; bootstrap lane, idempotent skip и стадии ``APPLY_DESIGN`` / ``APPLY_DESIGN_REVIEW``."""
    if state.get("implementation_force_design_hop"):
        return _design_redo_route_patch(state)
    if design_lane_forward_plan_in_state(state):
        return {}
    change_id = str(state.get("change_id") or "").strip()
    patch: dict[str, Any] = {}
    if state.get("artifact_bundle") is None:
        patch.update(_design_lane_bootstrap_patch(state))
        if patch.get("apply_design_lane_complete"):
            return patch
    merged_for_plan: GraphState = {**state, **patch}
    repo_root = plan_hop.resolved_repo_root(merged_for_plan)
    from workflows.implementation.routing_contract import SuperImplementPlanStep

    step = SuperImplementPlanStep(
        status=RUN_STATUS_RUNNING,
        live_execution_authorized=bool(merged_for_plan.get("live_execution_authorized")),
        next_stage=STAGE_APPLY_DESIGN,
        raw={},
    )
    plan_patch = plan_hop.build_design_lane_plan_patch(
        step,
        change_id=change_id,
        repo_root=repo_root,
        prev_stage=str(merged_for_plan.get("implementation_stage") or ""),
        prev_slice=str(merged_for_plan.get("implementation_slice_id") or ""),
        design_attempt=int(merged_for_plan.get("implementation_design_attempt") or 0),
    )
    patch.update(plan_patch)
    return patch


def _resolve_design_lane_agent(stage: str, agents: DesignLaneAgents) -> Any | None:
    if stage == STAGE_APPLY_DESIGN:
        return agents.apply_design
    if stage == STAGE_APPLY_DESIGN_REVIEW:
        return agents.apply_design_review
    return None


async def apply_design_lane_execute_node(
    state: GraphState,
    *,
    agents: DesignLaneAgents,
) -> dict[str, Any]:
    """Вызов адаптера агента только для design / design-review."""
    change_id = str(state.get("change_id") or "").strip()
    stage = str(state.get("implementation_stage") or "")
    repo_root = plan_hop.resolved_repo_root(state)
    if stage == STAGE_APPLY_DESIGN_REVIEW and is_apply_design_review_fresh(
        change_id,
        repo_root=repo_root,
    ):
        return plan_hop.continue_after_design_review(
            {"apply_design_review_status": "APPLY_DESIGN_REVIEW_PASS"},
            design_attempt=int(state.get("implementation_design_attempt") or 0),
        )
    if stage not in DESIGN_LANE_STAGES:
        return {
            "implementation_status": RUN_STATUS_FAILED,
            "implementation_recovery_code": "DESIGN_LANE_STAGE_MISMATCH",
            "error": f"design lane does not run stage {stage!r}",
        }
    if _dry_run_enabled():
        dry_patch: dict[str, Any] = {
            "implementation_last_hop": stage,
            "implementation_dry_run": True,
        }
        if stage == STAGE_APPLY_DESIGN:
            dry_patch["implementation_force_design_hop"] = False
        return dry_patch

    agent = _resolve_design_lane_agent(stage, agents)
    if agent is None:
        return {
            "implementation_status": RUN_STATUS_PAUSED,
            "implementation_recovery_code": "STAGE_NOT_WIRED",
            "error": f"no design lane agent for stage {stage!r}",
        }
    slice_id = str(state.get("implementation_slice_id") or "") or None
    if (
        stage == STAGE_APPLY_DESIGN
        and str(state.get("apply_design_status") or "") == "NO_CHANGE"
        and str(state.get("implementation_last_hop") or "") == STAGE_APPLY_DESIGN
    ):
        return plan_hop.continue_design_with_review(
            {**state, "implementation_last_hop": STAGE_APPLY_DESIGN},
        )
    hop_state: GraphState = {
        **state,
        "mode": stage.lower(),
        "answer": graph_dispatch_answer(state, slice_id),
    }
    hop_log_run_id = str(state.get("run_id") or "").strip() or ""
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
        hop_end_run_id = (
            str(result.get("run_id") or hop_log_run_id or "").strip() or hop_log_run_id
        )
    except ActiveCursorAgentRunError as exc:
        if exc.run_id:
            hop_end_run_id = str(exc.run_id).strip()
        return patch_implementation_adapter_outcome_unknown(
            stage=stage,
            slice_id=slice_id,
            agent_id=exc.agent_id,
            run_id=exc.run_id,
            exc=exc,
        )
    finally:
        _LOG.info(
            "implementation hop end change_id=%s procedure=%s run_id=%s",
            change_id,
            procedure,
            hop_end_run_id,
        )
    merged = {**result, "implementation_last_hop": stage}
    if stage == STAGE_APPLY_DESIGN:
        merged["implementation_arbiter_directive"] = None
        merged["implementation_arbiter_target_stage"] = None
        merged["implementation_arbiter_directed_design_hop"] = False
        return plan_hop.continue_design_with_review(merged)
    if stage == STAGE_APPLY_DESIGN_REVIEW:
        return plan_hop.continue_after_design_review(
            merged,
            design_attempt=int(state.get("implementation_design_attempt") or 0),
        )
    return merged


def build_apply_design_lane_graph(
    hop_agents: DesignLaneAgents,
    *,
    checkpointer=None,
):
    """Compiled subgraph design+review перед основным apply loop."""
    builder = StateGraph(GraphState)
    _ = builder.add_node(DESIGN_LANE_ROUTE_NODE, apply_design_lane_route_node)
    _ = builder.add_node(
        DESIGN_LANE_EXECUTE_NODE,
        partial(apply_design_lane_execute_node, agents=hop_agents),
    )

    _ = builder.add_edge(START, DESIGN_LANE_ROUTE_NODE)
    _ = builder.add_conditional_edges(
        DESIGN_LANE_ROUTE_NODE,
        route_after_design_lane_route,
        {
            DESIGN_LANE_EXECUTE_NODE: DESIGN_LANE_EXECUTE_NODE,
            END: END,
        },
    )
    _ = builder.add_conditional_edges(
        DESIGN_LANE_EXECUTE_NODE,
        route_after_design_lane_execute,
        {
            DESIGN_LANE_EXECUTE_NODE: DESIGN_LANE_EXECUTE_NODE,
            DESIGN_LANE_ROUTE_NODE: DESIGN_LANE_ROUTE_NODE,
            END: END,
        },
    )
    return builder.compile(checkpointer=checkpointer)


def design_lane_agents_from_hop_agents(
    hop_agents: Any,
) -> DesignLaneAgents:
    """Собрать ``DesignLaneAgents`` из полного ``ImplementationHopAgents``."""
    return DesignLaneAgents(
        apply_design=hop_agents.apply_design,
        apply_design_review=hop_agents.apply_design_review,
    )
