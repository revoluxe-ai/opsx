"""Связка split-apply hop answer → ``implementation_status`` для LangGraph routing.

ApplyRedAgent и сиблинги пишут только ``apply_*_status``. Routing в
``workflows/implementation/routing.py`` смотрит на ``implementation_status``:
``RUNNING`` → plan, ``PAUSED``/``FAILED`` → END.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from agents.implementation.prompt.stage_receipt_preservation import operative_receipt_from_plan
from workflows.implementation.slice_receipt_dispatch import (
    SliceReceiptValidationAnswer,
    parse_slice_receipt_validation_answer,
)
from workflows.implementation.routing_contract import (
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    implementation_lane_error_detail,
)

HopAnswerStatus = Literal[
    "APPLY_COMPLETE",
    "APPLY_PARTIAL",
    "BLOCKED",
    "NO_CHANGE",
    "ESCALATE_ARBITER",
    "APPLY_DESIGN_REVIEW_PASS",
    "REPAIRS_REQUIRED",
]

# Legacy handoff. Graph routing reads ``implementation_status`` only.
_LEGACY_HANDOFF_KEYS = frozenset({"next_command", "local_hop_copy_block", "next_action"})


def graph_dispatch_answer(
    state: Mapping[str, Any],
    slice_id: str | None,
) -> dict[str, Any]:
    """Контекст следующего hop: dispatched slice и поля без legacy handoff.

    ``next_command`` / ``local_hop_copy_block`` / ``next_action`` не попадают в prompt.
    Стадию выбирает plan-узел графа по ``implementation_status``.
    """
    raw = state.get("answer")
    cleaned: dict[str, Any] = {}
    if isinstance(raw, dict):
        cleaned = {key: value for key, value in raw.items() if key not in _LEGACY_HANDOFF_KEYS}
    if slice_id:
        cleaned["slice_id"] = slice_id
    receipt_dispatch = state.get("implementation_slice_receipt_validation")
    if isinstance(receipt_dispatch, dict):
        validated = parse_slice_receipt_validation_answer(receipt_dispatch)
        cleaned["slice_receipt_validation"] = validated.model_dump(mode="json")
    else:
        operative = operative_receipt_from_plan(state)
        if operative is not None:
            cleaned["operative_receipt"] = operative
    directive = str(state.get("implementation_arbiter_directive") or "").strip()
    target_stage = str(state.get("implementation_arbiter_target_stage") or "").strip().upper()
    hop_stage = str(state.get("implementation_stage") or "").strip().upper()
    if directive and target_stage and hop_stage == target_stage:
        cleaned["arbiter_directive"] = directive
    reject = str(state.get("implementation_plan_reject_code") or "").strip()
    attempt = int(state.get("implementation_unchanged_dispatch_count") or 0)
    if reject and attempt:
        cleaned["plan_rejected_code"] = reject
        cleaned["unchanged_dispatch_attempt"] = attempt
    machine_errors = state.get("implementation_machine_gate_errors")
    if isinstance(machine_errors, list) and machine_errors:
        cleaned["machine_gate_errors"] = list(machine_errors)
    else:
        operative = state.get("apply_red_operative_validation")
        if isinstance(operative, dict) and not operative.get("ok"):
            raw_errors = operative.get("errors")
            if isinstance(raw_errors, list) and raw_errors:
                cleaned["machine_gate_errors"] = [
                    e for e in raw_errors if isinstance(e, dict)
                ]
    return cleaned


def _arbiter_request_from_patch(result: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Извлечь ``arbiter_request`` из typed ``apply_*_answer`` в patch hop-агента."""
    if result is None:
        return None
    answer = _apply_hop_answer_from_patch(result)
    if answer is None:
        return None
    raw = answer.get("arbiter_request")
    if isinstance(raw, dict):
        return raw
    return None


def _apply_hop_answer_from_patch(result: Mapping[str, Any]) -> dict[str, Any] | None:
    """Найти typed ``apply_*_answer`` в patch hop-агента."""
    for key, value in result.items():
        if (
            key.startswith("apply_")
            and key.endswith("_answer")
            and isinstance(value, dict)
        ):
            return value
    return None


def _mapping_has_machine_blockers(mapping: Mapping[str, Any]) -> bool:
    """True, если в mapping есть непустые machine-gate ошибки или operative FAIL."""
    errors = mapping.get("implementation_machine_gate_errors")
    if isinstance(errors, list) and errors:
        return True
    operative = mapping.get("apply_red_operative_validation")
    if isinstance(operative, dict) and operative.get("ok") is False:
        return True
    return False


def no_change_lane_should_advance(
    result: Mapping[str, Any] | None,
    hop_state: Mapping[str, Any] | None,
) -> bool:
    """NO_CHANGE при machine PASS / operative-valid receipt → lane продвигается (RUNNING).

    Без доказательств (нет PASS gate, plan не authorized, есть ``remaining_gaps``) — False.
    """
    if result is None:
        return False
    for mapping in (result, hop_state or {}):
        if _mapping_has_machine_blockers(mapping):
            return False
    answer = _apply_hop_answer_from_patch(result)
    if answer is None:
        return False
    remaining = answer.get("remaining_gaps")
    if isinstance(remaining, list) and remaining:
        return False
    # Явный сброс machine errors после PASS (design / RED hop).
    if "implementation_machine_gate_errors" in result and result.get(
        "implementation_machine_gate_errors"
    ) is None:
        return True
    for mapping in (result, hop_state or {}):
        if str(mapping.get("implementation_receipt_gate") or "") == "SKIP":
            return True
        receipt_dispatch = mapping.get("implementation_slice_receipt_validation")
        if isinstance(receipt_dispatch, dict) and receipt_dispatch.get("valid"):
            return True
    operative = operative_receipt_from_plan(hop_state or {})
    if operative and operative.get("authorized"):
        return True
    return False


def _with_concrete_error(
    patch: dict[str, Any],
    result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Дописать ``error`` из machine gate или remaining_gaps, если текст есть."""
    detail = implementation_lane_error_detail(result or {})
    if detail:
        patch["error"] = detail
    return patch


def _paused_block_patch(result: Mapping[str, Any] | None) -> dict[str, Any]:
    """PAUSED + ``APPLY_BLOCKED`` вместе с конкретной причиной, если она известна."""
    return _with_concrete_error(
        {
            "implementation_status": RUN_STATUS_PAUSED,
            "implementation_recovery_code": "APPLY_BLOCKED",
        },
        result,
    )


def implementation_lane_patch_for_hop_answer(
    hop_status: str,
    *,
    result: Mapping[str, Any] | None = None,
    hop_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Перевести schema status hop-агента в patch lane для conditional edges.

    При ``PAUSED`` / ``BLOCKED`` в ``error`` попадает текст machine gate или
    ``remaining_gaps``, а не пустая строка.
    """
    if hop_status in ("APPLY_COMPLETE", "APPLY_PARTIAL"):
        return {"implementation_status": RUN_STATUS_RUNNING}
    if hop_status == "NO_CHANGE":
        if no_change_lane_should_advance(result, hop_state):
            return {"implementation_status": RUN_STATUS_RUNNING}
        return _paused_block_patch(result)
    if hop_status == "BLOCKED":
        if result is not None:
            explicit_status = str(result.get("implementation_status") or "")
            explicit_code = str(result.get("implementation_recovery_code") or "")
            if explicit_status in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED) and explicit_code:
                patch = {
                    "implementation_status": explicit_status,
                    "implementation_recovery_code": explicit_code,
                }
                return _with_concrete_error(patch, result)
        return _paused_block_patch(result)
    if hop_status == "ESCALATE_ARBITER":
        req = _arbiter_request_from_patch(result)
        patch: dict[str, Any] = {
            "implementation_status": RUN_STATUS_RUNNING,
            "implementation_arbiter_pending": True,
        }
        if req is not None:
            patch["implementation_arbiter_request"] = req
        return patch
    return {}


def merge_split_apply_hop_result(
    result: dict[str, Any],
    hop_status: str,
    *,
    lane_override: dict[str, Any] | None = None,
    hop_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Объединить SDK patch агента с lane status (optional override, напр. design review)."""
    if lane_override is not None:
        lane = lane_override
    else:
        lane = implementation_lane_patch_for_hop_answer(
            hop_status,
            result=result,
            hop_state=hop_state,
        )
    return {**result, **lane}
