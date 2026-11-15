"""Снятие устаревшего PAUSE после ``skipped_*_integrity``, когда handoff уже COMPLETE."""

from __future__ import annotations

from typing import Any, Mapping

from agents.state import GraphState
from agents.verify.diagnostic.per_finding import build_per_finding_diagnostic
from agents.verify.diagnostic.schemas import DiagnosticResult
from agents.verify.group_error.diagnostics_input_integrity import (
    input_integrity_from_gate_diagnostics,
)
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import RepairOrderResult

_STALE_GROUP_ERROR_SKIP = frozenset({"skipped_integrity", "skipped_input_integrity"})
_STALE_DIAGNOSTIC_PAUSE_PREFIX = (
    "group_error hop skipped due to incomplete or invalid diagnostics input"
)


def reconciled_gate_input_integrity_complete(state: Mapping[str, Any]) -> bool:
    """True, если актуальный ``quality_gate_diagnostics`` даёт COMPLETE после reconcile."""
    diag = state.get("quality_gate_diagnostics")
    if not isinstance(diag, dict):
        return False
    return input_integrity_from_gate_diagnostics(diag).status == "COMPLETE"


def stale_group_error_skip_should_pause(state: GraphState) -> bool:
    """Нужен ли PAUSE из-за ``group_error_execution_status`` skipped_* при текущем handoff."""
    ge_status = str(state.get("group_error_execution_status") or "")
    if ge_status not in _STALE_GROUP_ERROR_SKIP:
        return False
    return not reconciled_gate_input_integrity_complete(state)


def diagnostic_pause_is_stale_skip(pause_reason: str | None) -> bool:
    """True, если ``diagnostic_result.pause_reason`` — устаревший skip group_error."""
    if not pause_reason:
        return False
    return pause_reason.startswith(_STALE_DIAGNOSTIC_PAUSE_PREFIX)


def refresh_diagnostic_after_reconciled_skip(
    state: GraphState,
    diagnostic: DiagnosticResult,
    repair_order: RepairOrderResult,
    *,
    resolved: set[str],
) -> tuple[DiagnosticResult | None, dict[str, Any]]:
    """Пересобрать per-finding diagnostic, если PAUSE устарел, а integrity уже COMPLETE.

    Returns:
        Обновлённый diagnostic или ``None``, если pending findings не осталось
        (сигнал для ``final_gates``). Второй элемент — patch для GraphState.
    """
    from agents.verify.dispatch_mode import resolve_verify_dispatch_mode

    if diagnostic.status != "PAUSE":
        return diagnostic, {}
    if not diagnostic_pause_is_stale_skip(diagnostic.pause_reason):
        return diagnostic, {}
    if not reconciled_gate_input_integrity_complete(state):
        return diagnostic, {}
    if resolve_verify_dispatch_mode(state) != "per_finding":
        return diagnostic, {}

    analysis_raw = state.get("analysis_result")
    if not isinstance(analysis_raw, dict):
        return diagnostic, {}
    from agents.verify.group_error.normalize_triage import normalize_group_error_result

    from pydantic import ValidationError

    try:
        analysis = normalize_group_error_result(
            GroupErrorResult.model_validate(analysis_raw),
        )
    except (TypeError, ValidationError):
        return diagnostic, {}
    try:
        refreshed = build_per_finding_diagnostic(
            repair_order,
            analysis,
            resolved=resolved,
        )
    except ValueError:
        return None, {}
    patch = {"diagnostic_result": refreshed.model_dump(mode="json")}
    return refreshed, patch
