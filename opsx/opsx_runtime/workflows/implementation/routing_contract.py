"""JSON-контракт между Python implementation lane и ``opsx-super-implement.mjs plan``.

Источник правды: ``planSuperImplementStep`` в
``opsx_tools/opsx-super-implement.mjs`` (stdout — один JSON object).

Обязательные поля (все plan-ответы):

- ``status``: ``RUNNING`` | ``PAUSED`` | ``FAILED`` | ``APPLY_COMPLETE``
- ``live_execution_authorized``: bool

При ``status == RUNNING`` (plan_only hop):

- ``next_stage``: ``APPLY_DESIGN`` | ``APPLY_DESIGN_REVIEW`` | ``APPLY_SCAFFOLD`` |
  ``APPLY_RED`` | ``APPLY_IMPLEMENT`` | ``APPLY_GREEN`` | ``RECOVERY_PAUSED`` (via transition)
- ``next_slice``: str | null
- ``primitive_command``: str (например ``/opsx-apply-red``)
- ``plan_only``: true

При ``status == APPLY_COMPLETE``:

- ``next_stage``: ``APPLY_COMPLETE``
- ``auto_verify_forbidden``: true (verify — отдельный subgraph)

При ``status == PAUSED`` | ``FAILED``:

- ``code``: str
- optional ``recovery_contract``

Python парсит через ``parse_super_implement_plan_payload``; не дублировать
``selectNextPipelineStage`` в runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping

from agents.agent_visible_text import strip_slash_commands_from_text
from workflows.implementation.gate_error_text import (
    expand_tautological_error_text,
    format_gate_error_line,
)

ImplementationPlanStatus = Literal["RUNNING", "PAUSED", "FAILED", "APPLY_COMPLETE"]

RUN_STATUS_RUNNING = "RUNNING"
RUN_STATUS_PAUSED = "PAUSED"
RUN_STATUS_FAILED = "FAILED"
RUN_STATUS_APPLY_COMPLETE = "APPLY_COMPLETE"

STAGE_APPLY_DESIGN = "APPLY_DESIGN"
STAGE_APPLY_DESIGN_REVIEW = "APPLY_DESIGN_REVIEW"
STAGE_APPLY_SCAFFOLD = "APPLY_SCAFFOLD"
STAGE_APPLY_RED = "APPLY_RED"
STAGE_APPLY_IMPLEMENT = "APPLY_IMPLEMENT"
STAGE_APPLY_GREEN = "APPLY_GREEN"
STAGE_APPLY_COMPLETE = "APPLY_COMPLETE"
STAGE_RECOVERY_PAUSED = "RECOVERY_PAUSED"

EXECUTABLE_STAGES = frozenset(
    {
        STAGE_APPLY_DESIGN,
        STAGE_APPLY_DESIGN_REVIEW,
        STAGE_APPLY_SCAFFOLD,
        STAGE_APPLY_RED,
        STAGE_APPLY_IMPLEMENT,
        STAGE_APPLY_GREEN,
    },
)


@dataclass(frozen=True)
class SuperImplementPlanStep:
    """Нормализованный ответ ``planSuperImplementStep`` / CLI ``plan``."""

    status: ImplementationPlanStatus
    live_execution_authorized: bool
    next_stage: str | None = None
    next_slice: str | None = None
    primitive_command: str | None = None
    code: str | None = None
    recovery_contract: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None


def plan_transition_patch_for_step(step: SuperImplementPlanStep) -> dict[str, Any]:
    """Собрать patch state из plan step (RUNNING/PAUSED/FAILED/APPLY_COMPLETE)."""
    patch: dict[str, Any] = {
        "live_execution_authorized": step.live_execution_authorized,
        "implementation_plan": step.raw,
        "implementation_recovery_code": step.code,
    }
    if step.status in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED):
        patch["implementation_status"] = step.status
        if step.next_stage:
            patch["implementation_stage"] = step.next_stage
        if step.next_slice is not None:
            patch["implementation_slice_id"] = step.next_slice
        if step.recovery_contract is not None:
            patch["implementation_recovery_contract"] = step.recovery_contract
        return patch
    if step.status == RUN_STATUS_APPLY_COMPLETE:
        patch.update(
            {
                "implementation_status": RUN_STATUS_APPLY_COMPLETE,
                "implementation_stage": STAGE_APPLY_COMPLETE,
                "implementation_slice_id": None,
            },
        )
        return patch
    next_stage = str(step.next_stage or "")
    if next_stage == STAGE_RECOVERY_PAUSED:
        patch.update(
            {
                "implementation_status": RUN_STATUS_PAUSED,
                "implementation_stage": next_stage,
                "implementation_slice_id": step.next_slice,
            },
        )
        if step.recovery_contract is not None:
            patch["implementation_recovery_contract"] = step.recovery_contract
        return patch
    patch.update(
        {
            "implementation_status": RUN_STATUS_RUNNING,
            "implementation_stage": step.next_stage,
            "implementation_slice_id": step.next_slice,
            "implementation_primitive_command": step.primitive_command,
        },
    )
    return patch


def _effective_implementation_stage(state: Mapping[str, Any]) -> str:
    """Стадия lane: typed поле или ``next_stage`` из последнего plan JSON."""
    raw = state.get("implementation_plan")
    if isinstance(raw, dict):
        step = parse_super_implement_plan_payload(raw)
        if step.status == RUN_STATUS_APPLY_COMPLETE:
            return STAGE_APPLY_COMPLETE
    stage = str(state.get("implementation_stage") or "").strip()
    if stage:
        return stage
    if not isinstance(raw, dict):
        return ""
    step = parse_super_implement_plan_payload(raw)
    return str(step.next_stage or "").strip()


def implementation_lane_should_execute(state: Mapping[str, Any]) -> bool:
    """True, если после plan hop маршрут должен идти в execute, а не в END.

    Ephemeral verify-impl не использует stale ``Command(goto=…)``; внутреннее
    conditional edge должно читать plan JSON и forward stage, иначе subgraph
    останавливается с ``implementation_status=RUNNING`` на END (routing bug).
    """
    status = str(state.get("implementation_status") or "").strip()
    if status == RUN_STATUS_APPLY_COMPLETE:
        return False
    if str(state.get("implementation_stage") or "").strip() == STAGE_APPLY_COMPLETE:
        return False
    raw_early = state.get("implementation_plan")
    if isinstance(raw_early, dict):
        early = parse_super_implement_plan_payload(raw_early)
        if early.status == RUN_STATUS_APPLY_COMPLETE:
            return False
    stage = _effective_implementation_stage(state)
    if stage in EXECUTABLE_STAGES:
        if status in (RUN_STATUS_RUNNING, "IN_PROGRESS"):
            return True
    raw = state.get("implementation_plan")
    if not isinstance(raw, dict):
        return False
    step = parse_super_implement_plan_payload(raw)
    if step.status != RUN_STATUS_RUNNING:
        return False
    next_stage = str(step.next_stage or "").strip()
    if next_stage == STAGE_RECOVERY_PAUSED or next_stage not in EXECUTABLE_STAGES:
        return False
    if status in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED):
        return True
    if status in (RUN_STATUS_RUNNING, "IN_PROGRESS"):
        return True
    return False


def parse_super_implement_plan_payload(payload: dict[str, Any]) -> SuperImplementPlanStep:
    """Разобрать JSON stdout ``node opsx_tools/opsx-super-implement.mjs plan``."""
    status_raw = str(payload.get("status") or RUN_STATUS_FAILED)
    if status_raw not in (
        RUN_STATUS_RUNNING,
        RUN_STATUS_PAUSED,
        RUN_STATUS_FAILED,
        RUN_STATUS_APPLY_COMPLETE,
    ):
        status_raw = RUN_STATUS_FAILED
    status: ImplementationPlanStatus = status_raw  # type: ignore[assignment]
    live = bool(payload.get("live_execution_authorized"))
    next_stage = payload.get("next_stage")
    if next_stage is not None:
        next_stage = str(next_stage)
    next_slice = payload.get("next_slice")
    if next_slice is not None:
        next_slice = str(next_slice)
    primitive = payload.get("primitive_command")
    if primitive is not None:
        primitive = str(primitive)
    code = payload.get("code") or payload.get("recovery_code")
    if code is not None:
        code = str(code)
    recovery = payload.get("recovery_contract")
    if recovery is not None and not isinstance(recovery, dict):
        recovery = None
    return SuperImplementPlanStep(
        status=status,
        live_execution_authorized=live,
        next_stage=next_stage,
        next_slice=next_slice,
        primitive_command=primitive,
        code=code,
        recovery_contract=recovery,
        raw=payload,
    )


def implementation_lane_error_detail(state: Mapping[str, Any]) -> str:
    """Конкретный текст стопа lane для CLI и checkpoint.

    Берёт непустой ``error``. Если его нет — сообщения
    ``implementation_machine_gate_errors``, затем ``remaining_gaps`` из
    ``apply_*_answer``, затем ``implementation_plan_reject_code``.

    Args:
        state: Patch hop-агента или итоговое состояние графа.

    Returns:
        Одна строка с причиной. Пустая строка, если конкретного текста нет.
    """
    direct = strip_slash_commands_from_text(
        expand_tautological_error_text(str(state.get("error") or "")),
    )
    if direct:
        return direct
    errors = state.get("implementation_machine_gate_errors")
    if isinstance(errors, list):
        lines: list[str] = []
        for entry in errors[:15]:
            if not isinstance(entry, dict):
                continue
            lines.append(format_gate_error_line(entry))
        if lines:
            return strip_slash_commands_from_text("; ".join(lines))
    for key, value in state.items():
        if not (
            isinstance(key, str)
            and key.startswith("apply_")
            and key.endswith("_answer")
            and isinstance(value, dict)
        ):
            continue
        gaps = value.get("remaining_gaps")
        if not isinstance(gaps, list):
            continue
        text = "; ".join(str(item).strip() for item in gaps[:10] if str(item).strip())
        if text:
            return strip_slash_commands_from_text(f"remaining_gaps: {text}")
    reject = str(state.get("implementation_plan_reject_code") or "").strip()
    return strip_slash_commands_from_text(reject)


def format_current_implementation_procedure(
    stage: str | None,
    slice_id: str | None,
) -> str:
    """Текущая процедура implementation: stage и slice_id рядом (логи, CLI).

    Args:
        stage: ``implementation_stage`` (например ``APPLY_SCAFFOLD``).
        slice_id: ``implementation_slice_id`` (например ``SLICE-001``).

    Returns:
        ``APPLY_SCAFFOLD SLICE-001`` или отдельно stage/slice, если одно поле пустое.
    """
    name = str(stage or "").strip() or "(none)"
    sid = str(slice_id or "").strip()
    if sid and name != "(none)":
        return f"{name} {sid}"
    if sid:
        return sid
    return name
