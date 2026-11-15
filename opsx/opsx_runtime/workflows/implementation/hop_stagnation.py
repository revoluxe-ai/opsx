"""Plan после hop: повтор dispatch или продвижение stage (APPLY_COMPLETE / NO_CHANGE)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.implementation.hop_lane_status import no_change_lane_should_advance
from agents.state import GraphState
from workflows.implementation.routing_contract import (
    RUN_STATUS_APPLY_COMPLETE,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_COMPLETE,
    plan_transition_patch_for_step,
)
from workflows.implementation import stage_dispatch
from workflows.implementation.ledger import read_live_execution_authorized
from workflows.repo_paths import resolve_opsx_repository_root

# Один повторный execute того же stage+slice, затем PAUSED.
_UNCHANGED_DISPATCH_RETRY_LIMIT = 1


def _operative_code(step: Any) -> str:
    """Код гейта из plan, если stage не сдвинулся."""
    operative = (step.raw or {}).get("prior_stage_operative")
    if isinstance(operative, dict) and operative.get("code"):
        return str(operative["code"])
    if step.code:
        return str(step.code)
    return "DISPATCH_UNCHANGED_AFTER_HOP"


def stagnation_patch_after_hop(
    state: GraphState,
    *,
    executed_stage: str,
    executed_slice: str | None,
    hop_status: str,
    hop_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Повтор того же stage, если plan не сдвинулся; второй раз — PAUSED.

    Typed ``APPLY_COMPLETE`` не выбирает следующую стадию. Её выбирает plan.
    Пока plan возвращает тот же stage+slice, первый ответ не заканчивает lane.

    ``NO_CHANGE`` с operative PASS (например SCAFFOLD_READY): тот же plan refresh,
    иначе следующий visit снова вызовет plan без сдвига stage → loop scaffold.
    """
    if hop_status == "NO_CHANGE":
        if not no_change_lane_should_advance(hop_result, state):
            return {}
    elif hop_status not in ("APPLY_COMPLETE", "APPLY_PARTIAL"):
        return {}
    change_id = str(state.get("change_id") or "").strip()
    if not change_id:
        return {}
    repo_root = resolve_opsx_repository_root(Path(str(state["repository_path"])))
    live = read_live_execution_authorized(
        change_id,
        repo_root=repo_root,
        state_flag=state.get("live_execution_authorized"),  # type: ignore[arg-type]
    )
    step = stage_dispatch.plan_super_implement_step(
        change_id,
        repo_root=repo_root,
        live_execution_authorized=live,
    )
    plan_cache = {
        "implementation_plan": step.raw,
        "implementation_plan_reuse_after_hop": True,
    }
    if step.status != RUN_STATUS_RUNNING:
        if step.status == RUN_STATUS_APPLY_COMPLETE:
            return {
                **plan_cache,
                "implementation_status": RUN_STATUS_APPLY_COMPLETE,
                "implementation_stage": STAGE_APPLY_COMPLETE,
            }
        return {}
    if step.next_stage != executed_stage or (step.next_slice or None) != (executed_slice or None):
        return {
            "implementation_unchanged_dispatch_count": 0,
            "implementation_plan_reject_code": None,
            **plan_cache,
            **plan_transition_patch_for_step(step),
        }
    code = _operative_code(step)
    seen = int(state.get("implementation_unchanged_dispatch_count") or 0)
    if seen < _UNCHANGED_DISPATCH_RETRY_LIMIT:
        return {
            "implementation_status": RUN_STATUS_RUNNING,
            "implementation_recovery_code": code,
            "implementation_plan_reject_code": code,
            "implementation_unchanged_dispatch_count": seen + 1,
            **plan_cache,
        }
    return {
        "implementation_status": RUN_STATUS_PAUSED,
        "implementation_recovery_code": code,
        "implementation_plan_reject_code": code,
        "implementation_last_hop": executed_stage,
        "implementation_unchanged_dispatch_count": seen + 1,
        **plan_cache,
    }
