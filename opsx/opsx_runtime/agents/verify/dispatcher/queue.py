"""Очередь одного прохода Dispatcher после Diagnostic.

Источник элементов (repair-unit level):

1. Если ``diagnostic_result.status == DIAGNOSED`` и есть ``repair_unit`` — один элемент
   очереди на всю unit (``item_kind=repair_unit``); ``remaining_queue`` **не** входит в pass.
2. Иначе fallback: ``repair_order_result.ordered_units[0]`` — один finding или repair unit
   при нескольких ``finding_ids`` в head unit.

Хвост ``ordered_units`` / ``remaining_queue`` обрабатывается в outer batch
(``workflows.verify.outer_batch``): shrink ``repair_order``, anchor-only diagnostic,
``dispatch_cycle`` без повторного GroupError/RepairOrder до финальных quality gates.
"""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.diagnostic.schemas import DiagnosticResult
from agents.verify.dispatch_mode import resolve_verify_dispatch_mode
from agents.verify.dispatch_unit.queue_items import build_repair_unit_queue_item
from agents.verify.dispatcher.flatten_order import pending_finding_ids
from agents.verify.dispatcher.items import finding_item_from_finding
from agents.verify.group_error.schemas import GroupErrorResult, Finding
from agents.verify.repair_order.schemas import RepairOrderResult


def parse_path_line(observation: str) -> tuple[str | None, int | None]:
    """Re-export для совместимости импортов."""
    from agents.verify.dispatcher.items import parse_path_line as _parse

    return _parse(observation)


def _findings_by_id(analysis: GroupErrorResult | None) -> dict[str, Finding]:
    if analysis is None:
        return {}
    return {f.finding_id: f for f in analysis.findings}


def _finding_item(finding: Finding, state: GraphState) -> dict[str, Any]:
    diag = state.get("quality_gate_diagnostics")
    diagnostics = diag if isinstance(diag, dict) else None
    return finding_item_from_finding(finding, quality_gate_diagnostics=diagnostics)


def _findings_for_ids(
    finding_ids: list[str],
    findings_map: dict[str, Finding],
) -> list[Finding]:
    out: list[Finding] = []
    for fid in finding_ids:
        f = findings_map.get(fid)
        if f is not None:
            out.append(f)
    return out


def _resolved_finding_ids(state: GraphState) -> set[str]:
    raw = state.get("dispatcher_resolved_finding_ids")
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw if x}


def build_dispatcher_pass_queue(state: GraphState) -> tuple[list[dict[str, Any]], str]:
    """Собрать очередь pass (repair unit items) и статус инициализации."""
    analysis_raw = state.get("analysis_result")
    analysis = (
        GroupErrorResult.model_validate(analysis_raw)
        if isinstance(analysis_raw, dict)
        else None
    )
    findings_map = _findings_by_id(analysis)

    diagnostic_raw = state.get("diagnostic_result")
    diagnostic: DiagnosticResult | None = None
    if isinstance(diagnostic_raw, dict):
        diagnostic = DiagnosticResult.model_validate(diagnostic_raw)

    repair_order_raw = state.get("repair_order_result")
    repair_order: RepairOrderResult | None = None
    if isinstance(repair_order_raw, dict):
        repair_order = RepairOrderResult.model_validate(repair_order_raw)

    if (
        resolve_verify_dispatch_mode(state) == "per_finding"
        and repair_order is not None
        and repair_order.ordered_units
    ):
        resolved = _resolved_finding_ids(state)
        pending = pending_finding_ids(repair_order, resolved=resolved)
        if pending:
            head = pending[0]
            finding = findings_map.get(head)
            if finding is not None:
                return [_finding_item(finding, state)], "READY_PER_FINDING"
        return [], "SKIPPED_EMPTY"

    diagnostics_raw = state.get("quality_gate_diagnostics")
    quality_gate_diagnostics = diagnostics_raw if isinstance(diagnostics_raw, dict) else None

    if diagnostic is not None and diagnostic.status == "DIAGNOSED" and diagnostic.repair_unit:
        repair = diagnostic.repair_unit
        finding_ids = list(repair.finding_ids)
        findings = _findings_for_ids(finding_ids, findings_map)
        unit_item = build_repair_unit_queue_item(
            finding_ids=finding_ids,
            findings=findings,
            anchor_unit_id=diagnostic.anchor_unit_id,
            root_cause_summary=repair.root_cause_summary,
            change_boundaries=repair.change_boundaries,
            quality_gate_diagnostics=quality_gate_diagnostics,
        )
        return [unit_item], "READY"

    if isinstance(repair_order_raw, dict) and repair_order is not None:
        if repair_order.ordered_units:
            head = repair_order.ordered_units[0]
            fids = list(head.finding_ids)
            if len(fids) > 1:
                findings = _findings_for_ids(fids, findings_map)
                unit_item = build_repair_unit_queue_item(
                    finding_ids=fids,
                    findings=findings,
                    anchor_unit_id=head.unit_id,
                    root_cause_summary="fallback repair order head unit",
                    change_boundaries="minimal fallback boundaries",
                    quality_gate_diagnostics=quality_gate_diagnostics,
                )
                return [unit_item], "READY_FALLBACK_ORDER"
            if fids:
                finding = findings_map.get(fids[0])
                if finding is not None:
                    return [_finding_item(finding, state)], "READY_FALLBACK_ORDER"

    if diagnostic is not None and diagnostic.status == "PAUSE":
        return [], "SKIPPED_DIAGNOSTIC_PAUSE"

    return [], "SKIPPED_EMPTY"


def ensure_dispatcher_pass_initialized(state: GraphState) -> dict[str, Any]:
    """Инициализировать поля прохода, если ещё не заданы."""
    if state.get("dispatcher_pass_initialized"):
        return {}
    queue, init_status = build_dispatcher_pass_queue(state)
    patch: dict[str, Any] = {
        "dispatcher_pass_initialized": True,
        "dispatcher_pass_queue": queue,
        "dispatcher_pass_index": 0,
        "dispatcher_pass_complete": not queue,
        "dispatcher_cycle_status": init_status,
        "dispatcher_unit_triage_complete": False,
        "dispatch_causal_groups": [],
        "dispatcher_group_index": 0,
        "dispatcher_group_phase": "repair",
        "dispatch_unit_triage_result": None,
        "dispatch_group_verify_result": None,
        "dispatcher_current_group": None,
    }
    if state.get("dispatcher_outcomes") is None:
        patch["dispatcher_outcomes"] = []
    if state.get("dispatcher_resolved_finding_ids") is None:
        patch["dispatcher_resolved_finding_ids"] = []
    return patch
