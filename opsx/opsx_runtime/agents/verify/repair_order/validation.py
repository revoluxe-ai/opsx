"""Инварианты ``RepairOrderResult`` относительно входного ``GroupErrorResult``."""

from __future__ import annotations

from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import RepairOrderResult


def validate_unit_coverage(
    result: RepairOrderResult,
    analysis: GroupErrorResult,
) -> None:
    """Каждый finding ID из triage — ровно один раз в ``ordered_units``."""
    if result.status == "PAUSE":
        return
    expected = {f.finding_id for f in analysis.findings}
    if not expected:
        return
    seen: set[str] = set()
    duplicates: list[str] = []
    for unit in result.ordered_units:
        for fid in unit.finding_ids:
            if fid in seen:
                duplicates.append(fid)
            seen.add(fid)
    if duplicates:
        raise ValueError(
            "finding IDs appear more than once in ordered_units: "
            + ", ".join(sorted(set(duplicates))),
        )
    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    if missing or extra:
        raise ValueError(
            "ordered_units finding coverage mismatch: "
            f"missing={missing[:20]}{'...' if len(missing) > 20 else ''} "
            f"extra={extra[:20]}{'...' if len(extra) > 20 else ''}",
        )


def validate_no_regroup(
    result: RepairOrderResult,
    analysis: GroupErrorResult,
) -> None:
    """``finding_ids`` unit-ов совпадают с ``grouped_errors`` из triage (без regroup)."""
    if result.status == "PAUSE":
        return
    error_map = {row.error_id: list(row.finding_ids) for row in analysis.grouped_errors}
    seen_error_ids: set[str] = set()
    for unit in result.ordered_units:
        if unit.unit_type != "GROUP":
            raise ValueError(
                f"invalid unit_type {unit.unit_type!r}: only GROUP units are expected "
                "after group_error schema 1.5",
            )
        if unit.unit_id in seen_error_ids:
            raise ValueError(f"duplicate GROUP unit_id {unit.unit_id}")
        seen_error_ids.add(unit.unit_id)
        expected = error_map.get(unit.unit_id)
        if expected is None:
            raise ValueError(f"unknown error_id in ordered_units: {unit.unit_id}")
        if list(unit.finding_ids) != list(expected):
            raise ValueError(
                f"error {unit.unit_id}: finding_ids must match triage exactly, "
                f"expected {expected}, got {list(unit.finding_ids)}",
            )
    if seen_error_ids != set(error_map):
        missing = sorted(set(error_map) - seen_error_ids)
        extra = sorted(seen_error_ids - set(error_map))
        if missing or extra:
            raise ValueError(
                "GROUP units must cover triage grouped_errors exactly: "
                f"missing={missing} extra={extra}",
            )


def validate_positions(result: RepairOrderResult) -> None:
    """Позиции 1..N без пропусков и дубликатов."""
    if result.status == "PAUSE":
        return
    n = len(result.ordered_units)
    if n == 0:
        return
    positions = [u.position for u in result.ordered_units]
    expected = list(range(1, n + 1))
    if positions != expected:
        raise ValueError(
            f"ordered_units positions must be {expected}, got {positions}",
        )
