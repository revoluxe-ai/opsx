"""Детерминированный diagnostic для режима ``per_finding`` (без repair unit batch)."""

from __future__ import annotations

from agents.verify.diagnostic.schemas import (
    DiagnosticResult,
    RemainingQueueUnit,
    source_identity_from_repair_order,
)
from agents.verify.dispatcher.flatten_order import (
    findings_map,
    flatten_ordered_finding_ids,
    pending_finding_ids,
)
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import RepairOrderResult


def _unit_for_finding(
    repair_order: RepairOrderResult,
    finding_id: str,
) -> RemainingQueueUnit | None:
    for unit in repair_order.ordered_units:
        if finding_id in unit.finding_ids:
            return RemainingQueueUnit(
                position=unit.position,
                unit_type=unit.unit_type,
                unit_id=unit.unit_id,
            )
    return None


def build_per_finding_remaining_queue(
    repair_order: RepairOrderResult,
    *,
    head_finding_id: str,
    resolved: set[str],
) -> list[RemainingQueueUnit]:
    """Units из repair_order, у которых остаются неразрешённые findings после head."""
    flat = flatten_ordered_finding_ids(repair_order)
    try:
        start = flat.index(head_finding_id)
    except ValueError:
        start = 0
    tail_fids = [fid for fid in flat[start + 1 :] if fid not in resolved]
    unit_ids: list[str] = []
    out: list[RemainingQueueUnit] = []
    for fid in tail_fids:
        rq = _unit_for_finding(repair_order, fid)
        if rq is None or rq.unit_id in unit_ids:
            continue
        unit_ids.append(rq.unit_id)
        out.append(rq)
    return out


def build_per_finding_diagnostic(
    repair_order: RepairOrderResult,
    analysis: GroupErrorResult,
    *,
    resolved: set[str] | None = None,
) -> DiagnosticResult:
    """Один anchor finding, ``repair_unit=None``, remaining по repair_order."""
    resolved_set = resolved or set()
    pending = pending_finding_ids(repair_order, resolved=resolved_set)
    if not pending:
        raise ValueError("no pending findings in repair_order")
    head_fid = pending[0]
    by_id = findings_map(analysis)
    finding = by_id.get(head_fid)
    obs = str((finding.observation if finding else None) or "")[:200]
    anchor_unit = _unit_for_finding(repair_order, head_fid)
    anchor_unit_id = anchor_unit.unit_id if anchor_unit else head_fid
    boundaries = (
        f"Minimal fix for finding {head_fid} only; "
        f"do not batch unrelated findings. Evidence: {obs or 'see gate diagnostics'}"
    )
    return DiagnosticResult(
        status="DIAGNOSED",
        source_identity=source_identity_from_repair_order(repair_order),
        anchor_unit_id=anchor_unit_id,
        repair_unit=None,
        remaining_queue=build_per_finding_remaining_queue(
            repair_order,
            head_finding_id=head_fid,
            resolved=resolved_set,
        ),
        pause_reason=None,
    )


__all__ = [
    "build_per_finding_diagnostic",
    "build_per_finding_remaining_queue",
]
