"""Общая логика plan-hop: вызов Node plan и сбор state patch для route-узлов."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from agents.state import GraphState
from workflows.implementation import stage_dispatch
from workflows.implementation.design_machine_validation import (
    DesignDependentSnapshotResult,
    evaluate_apply_design_dependent_snapshot,
)
from workflows.implementation.design_pipeline.routing import DESIGN_LANE_STAGES
from workflows.implementation.ledger import read_live_execution_authorized
from workflows.implementation.routing_contract import (
    RUN_STATUS_APPLY_COMPLETE,
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_DESIGN,
    STAGE_APPLY_DESIGN_REVIEW,
    STAGE_APPLY_GREEN,
    STAGE_RECOVERY_PAUSED,
    SuperImplementPlanStep,
    plan_transition_patch_for_step,
)
from workflows.repo_paths import resolve_opsx_repository_root

PlanHopBranch = Literal["design", "main"]


def plan_override_from_state(state: Mapping[str, Any]) -> dict[str, Any] | None:
    override = state.get("implementation_plan_override")
    if isinstance(override, dict):
        return override
    return None


def clear_hop_counters_on_stage_change(
    patch: dict[str, Any],
    *,
    prev_stage: str,
    prev_slice: str,
    next_stage: str,
    next_slice: str,
) -> None:
    """Сброс machine-gate счётчиков при смене stage/slice (design и main loop)."""
    if next_stage != prev_stage or next_slice != prev_slice:
        patch["implementation_machine_gate_errors"] = None
        patch["implementation_hop_machine_repair_count"] = 0
        patch["implementation_plan_reject_code"] = None
        patch["apply_red_operative_validation"] = None


def resolve_plan_step(
    branch: PlanHopBranch,
    change_id: str,
    *,
    state: Mapping[str, Any],
    repo_root: Path,
    live_execution_authorized: bool,
    plan_payload_override: dict[str, Any] | None,
) -> tuple[SuperImplementPlanStep, bool]:
    """Вызвать plan subprocess или reuse; main branch — ``resolve_super_implement_plan_step``."""
    if branch == "design":
        step = stage_dispatch.plan_super_implement_step(
            change_id,
            repo_root=repo_root,
            live_execution_authorized=live_execution_authorized,
            plan_payload_override=plan_payload_override,
        )
        return step, False
    return stage_dispatch.resolve_super_implement_plan_step(
        change_id,
        state=state,
        repo_root=repo_root,
        live_execution_authorized=live_execution_authorized,
        plan_payload_override=plan_payload_override,
    )


def _snapshot_blocks_skip(snapshot: DesignDependentSnapshotResult) -> bool:
    """True когда design-файл есть, а snapshot зависимых файлов не сходится.

    ``APPLY_DESIGN_MISSING`` не перебивает plan: отсутствие файла уже
    возвращает стадию ``APPLY_DESIGN`` из Node plan.
    """
    if snapshot.ok:
        return False
    return snapshot.code != "APPLY_DESIGN_MISSING"


def _snapshot_redo_message(snapshot: DesignDependentSnapshotResult) -> str:
    """Текст для design-агента: почему snapshot зависимых файлов невалиден."""
    code = snapshot.code or "STALE_APPLY_DESIGN"
    frozen = snapshot.frozen_digest or ""
    current = snapshot.current_digest or ""
    return (
        f"{code}: design dependent-file snapshot does not match disk "
        f"(frozen={frozen}; current={current})"
    )


_MAX_DESIGN_ATTEMPTS = 3


def _slice_implementation_patch() -> dict[str, Any]:
    """Выход из design lane: дальше только имплементация слайсов."""
    return {
        "apply_design_lane_complete": True,
        "implementation_status": "IN_PROGRESS",
        "implementation_stage": None,
        "implementation_slice_id": None,
        "implementation_primitive_command": None,
        "implementation_machine_gate_errors": None,
        "implementation_plan_reject_code": None,
        "implementation_recovery_code": None,
        "implementation_force_design_hop": False,
    }


def _force_design_agent_patch(
    step: SuperImplementPlanStep,
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    """Не пропускать design: прогнать APPLY_DESIGN и передать причину в prompt."""
    return {
        "apply_design_lane_complete": False,
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_stage": STAGE_APPLY_DESIGN,
        "implementation_slice_id": step.next_slice,
        "implementation_primitive_command": "/opsx-apply-design",
        "implementation_machine_gate_errors": [{"code": code, "message": message}],
        "implementation_plan_reject_code": code,
        "implementation_recovery_code": code,
    }


def continue_design_with_review(hop_result: dict[str, Any]) -> dict[str, Any]:
    """После design hop следующая стадия только ``APPLY_DESIGN_REVIEW``.

    BLOCKED, эскалация арбитру и ``implementation_arbiter_pending`` не
    превращаются в RUNNING и не сбрасываются.
    """
    merged = dict(hop_result)
    status = str(merged.get("apply_design_status") or "")
    impl_status = str(merged.get("implementation_status") or "")
    if (
        status in ("BLOCKED", "ESCALATE_ARBITER")
        or impl_status == "BLOCKED"
        or merged.get("implementation_arbiter_pending")
    ):
        merged["implementation_last_hop"] = STAGE_APPLY_DESIGN
        merged["apply_design_lane_complete"] = False
        return merged
    merged["implementation_last_hop"] = STAGE_APPLY_DESIGN
    merged["implementation_stage"] = STAGE_APPLY_DESIGN_REVIEW
    merged["implementation_status"] = RUN_STATUS_RUNNING
    merged["implementation_primitive_command"] = "/opsx-apply-design-review"
    merged["apply_design_lane_complete"] = False
    merged["implementation_arbiter_pending"] = False
    merged["implementation_force_design_hop"] = False
    return merged


def continue_after_design_review(
    hop_result: dict[str, Any],
    *,
    design_attempt: int,
) -> dict[str, Any]:
    """После review: pass → слайсы, иначе новый design, но не больше трёх.

    ``APPLY_DESIGN_REVIEW_PASS`` — единственный pass (GO_PASS). Любой другой
    статус review возвращает ``APPLY_DESIGN``, пока попыток меньше трёх.
    """
    merged = dict(hop_result)
    merged["implementation_last_hop"] = STAGE_APPLY_DESIGN_REVIEW
    merged["implementation_arbiter_pending"] = False
    merged["implementation_force_design_hop"] = False
    status = str(merged.get("apply_design_review_status") or "")
    attempt = max(0, int(design_attempt))
    if status == "APPLY_DESIGN_REVIEW_PASS":
        merged.update(_slice_implementation_patch())
        merged["implementation_design_attempt"] = attempt
        return merged
    if attempt >= _MAX_DESIGN_ATTEMPTS:
        merged["apply_design_lane_complete"] = False
        merged["implementation_status"] = RUN_STATUS_PAUSED
        merged["implementation_stage"] = STAGE_APPLY_DESIGN_REVIEW
        merged["implementation_recovery_code"] = "DESIGN_REVIEW_ATTEMPTS_EXHAUSTED"
        merged["implementation_design_attempt"] = attempt
        return merged
    merged["apply_design_lane_complete"] = False
    merged["implementation_status"] = RUN_STATUS_RUNNING
    merged["implementation_stage"] = STAGE_APPLY_DESIGN
    merged["implementation_slice_id"] = None
    merged["implementation_primitive_command"] = "/opsx-apply-design"
    merged["implementation_design_attempt"] = attempt + 1
    merged["implementation_recovery_code"] = status or "REPAIRS_REQUIRED"
    return merged


def build_design_lane_plan_patch(
    step: SuperImplementPlanStep,
    *,
    change_id: str,
    repo_root: Path,
    prev_stage: str,
    prev_slice: str,
    design_attempt: int = 0,
) -> dict[str, Any]:
    """Вход в design lane: только первый ``APPLY_DESIGN``.

    Node ``next_stage`` здесь не выбирает review и не открывает слайсы.
    Review ставится сразу после design hop. Развилка design/слайсы — после review.
    """
    _ = prev_stage, prev_slice
    attempt = max(0, int(design_attempt))
    patch: dict[str, Any] = {
        "live_execution_authorized": step.live_execution_authorized,
        "implementation_plan": step.raw,
    }
    if attempt >= _MAX_DESIGN_ATTEMPTS:
        patch["apply_design_lane_complete"] = False
        patch["implementation_status"] = RUN_STATUS_PAUSED
        patch["implementation_recovery_code"] = "DESIGN_REVIEW_ATTEMPTS_EXHAUSTED"
        patch["implementation_design_attempt"] = attempt
        return patch
    snapshot = evaluate_apply_design_dependent_snapshot(change_id, repo_root=repo_root)
    if not snapshot.ok:
        code = snapshot.code or "STALE_APPLY_DESIGN"
        message = _snapshot_redo_message(snapshot)
    else:
        code = "APPLY_DESIGN"
        message = "APPLY_DESIGN, then design review"
    patch.update(_force_design_agent_patch(step, code=code, message=message))
    patch["implementation_design_attempt"] = 1 if attempt == 0 else attempt
    return patch


def build_main_lane_plan_patch(
    step: SuperImplementPlanStep,
    *,
    prev_stage: str,
    prev_slice: str,
    plan_reused: bool,
) -> dict[str, Any]:
    """State patch после main implementation_route plan hop."""
    patch: dict[str, Any] = {
        **plan_transition_patch_for_step(step),
        "implementation_plan_reuse_after_hop": False,
    }
    if step.status == RUN_STATUS_APPLY_COMPLETE:
        return patch
    if step.status in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED):
        return patch
    next_stage = str(step.next_stage or "")
    if next_stage in DESIGN_LANE_STAGES:
        contract = step.recovery_contract if isinstance(step.recovery_contract, dict) else {}
        message = str(contract.get("next_action") or contract.get("code") or "").strip()
        if not message:
            message = (
                "a hop concluded the design is wrong; redo design, then restart the slice"
            )
        code = str(step.code or "DESIGN_REDO")
        if prev_stage != STAGE_APPLY_GREEN:
            patch.update(
                {
                    "implementation_status": RUN_STATUS_PAUSED,
                    "implementation_recovery_contract": contract or None,
                    "implementation_machine_gate_errors": [
                        {
                            "code": "DESIGN_REDO_MID_SLICE_FORBIDDEN",
                            "message": (
                                "return to APPLY DESIGN only after APPLY_GREEN; "
                                f"plan requested design from {prev_stage!r}"
                            ),
                        },
                    ],
                    "implementation_recovery_code": "DESIGN_REDO_MID_SLICE_FORBIDDEN",
                    "error": message,
                },
            )
            return patch
        patch.update(
            {
                "implementation_force_design_hop": True,
                "apply_design_lane_complete": False,
                "implementation_recovery_contract": contract or None,
                "implementation_machine_gate_errors": [{"code": code, "message": message}],
                "implementation_recovery_code": code,
                "error": message,
            },
        )
        return patch
    if next_stage == STAGE_RECOVERY_PAUSED:
        return patch
    next_slice = str(step.next_slice or "")
    clear_hop_counters_on_stage_change(
        patch,
        prev_stage=prev_stage,
        prev_slice=prev_slice,
        next_stage=next_stage,
        next_slice=next_slice,
    )
    if plan_reused:
        patch["implementation_plan_reuse_after_hop"] = False
    return patch


def read_live_flag(state: GraphState, change_id: str, repo_root: Path) -> bool:
    """``live_execution_authorized`` из ledger с учётом state flag."""
    return read_live_execution_authorized(
        change_id,
        repo_root=repo_root,
        state_flag=state.get("live_execution_authorized"),  # type: ignore[arg-type]
    )


def resolved_repo_root(state: GraphState) -> Path:
    """Нормализованный repo root из ``repository_path``."""
    return resolve_opsx_repository_root(Path(str(state["repository_path"])))
