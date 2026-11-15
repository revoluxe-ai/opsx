"""Инварианты ``DiagnosticResult`` относительно repair_order и triage."""

from __future__ import annotations

from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import RepairOrderResult
from agents.verify.repair_order.validation import validate_no_regroup as validate_repair_order_no_regroup
from agents.verify.diagnostic.schemas import DiagnosticResult, RemainingQueueUnit


def _ordered_unit_map(repair_order: RepairOrderResult) -> dict[str, tuple[int, str, list[str]]]:
    """unit_id → (position, unit_type, finding_ids)."""
    out: dict[str, tuple[int, str, list[str]]] = {}
    for u in repair_order.ordered_units:
        out[u.unit_id] = (u.position, u.unit_type, list(u.finding_ids))
    return out


def validate_anchor_is_queue_head(result: DiagnosticResult, repair_order: RepairOrderResult) -> None:
    """``anchor_unit_id`` — первый unit в ``ordered_units``."""
    if result.status == "PAUSE":
        return
    if not repair_order.ordered_units:
        raise ValueError("repair_order.ordered_units empty")
    head = repair_order.ordered_units[0].unit_id
    if result.anchor_unit_id != head:
        raise ValueError(
            f"anchor_unit_id must be first ordered unit {head!r}, got {result.anchor_unit_id!r}",
        )


def validate_repair_unit_coverage(
    result: DiagnosticResult,
    repair_order: RepairOrderResult,
) -> None:
    """Repair unit покрывает целые repair_order units; finding_ids согласованы."""
    if result.status == "PAUSE":
        return
    if result.repair_unit is None:
        raise ValueError("repair_unit required when status is DIAGNOSED")
    unit_map = _ordered_unit_map(repair_order)
    repair = result.repair_unit
    if result.anchor_unit_id not in repair.unit_ids:
        raise ValueError("anchor_unit_id must appear in repair_unit.unit_ids")
    if repair.unit_ids[0] != result.anchor_unit_id:
        raise ValueError("repair_unit.unit_ids must start with anchor_unit_id")

    expected_finding_ids: list[str] = []
    for uid in repair.unit_ids:
        if uid not in unit_map:
            raise ValueError(f"unknown unit_id in repair_unit: {uid}")
        _, _, fids = unit_map[uid]
        expected_finding_ids.extend(fids)

    if list(repair.finding_ids) != expected_finding_ids:
        raise ValueError(
            "repair_unit.finding_ids must equal concatenation of included units' finding_ids "
            f"in repair_order order; expected {expected_finding_ids}, got {list(repair.finding_ids)}",
        )

    seen_fids: set[str] = set()
    for fid in repair.finding_ids:
        if fid in seen_fids:
            raise ValueError(f"duplicate finding_id in repair_unit: {fid}")
        seen_fids.add(fid)

    link_ids = {link.unit_id for link in repair.merged_unit_linkages}
    non_anchor = [uid for uid in repair.unit_ids if uid != result.anchor_unit_id]
    if non_anchor:
        missing_basis = sorted(set(non_anchor) - link_ids)
        if missing_basis:
            raise ValueError(
                "merged units require merged_unit_linkages entry: "
                + ", ".join(missing_basis),
            )
    extra_links = sorted(link_ids - set(repair.unit_ids))
    if extra_links:
        raise ValueError(
            "merged_unit_linkages references units not in repair_unit.unit_ids: "
            + ", ".join(extra_links),
        )


def validate_remaining_queue(
    result: DiagnosticResult,
    repair_order: RepairOrderResult,
) -> None:
    """``remaining_queue`` = ordered units minus repair_unit units, порядок сохранён."""
    if result.status == "PAUSE":
        return
    repair = result.repair_unit
    included = set(repair.unit_ids) if repair else set()
    expected: list[RemainingQueueUnit] = []
    for u in repair_order.ordered_units:
        if u.unit_id in included:
            continue
        expected.append(
            RemainingQueueUnit(
                position=u.position,
                unit_type=u.unit_type,
                unit_id=u.unit_id,
            ),
        )
    if result.remaining_queue != expected:
        raise ValueError(
            "remaining_queue must be ordered_units \\ repair_unit.unit_ids "
            f"expected {[x.unit_id for x in expected]}, "
            f"got {[x.unit_id for x in result.remaining_queue]}",
        )


def validate_no_regroup(
    result: DiagnosticResult,
    repair_order: RepairOrderResult,
    analysis: GroupErrorResult,
) -> None:
    """Не нарушать membership triage; repair_order уже валиден относительно triage."""
    validate_repair_order_no_regroup(repair_order, analysis)
    if result.status == "PAUSE" or result.repair_unit is None:
        return
    unit_map = _ordered_unit_map(repair_order)
    for uid in result.repair_unit.unit_ids:
        pos, utype, fids = unit_map[uid]
        _ = pos, utype
        for row in analysis.grouped_errors:
            if row.error_id == uid and list(fids) != list(row.finding_ids):
                raise ValueError(f"regroup detected for error {uid}")
