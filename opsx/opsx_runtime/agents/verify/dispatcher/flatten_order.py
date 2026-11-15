"""Развёртка repair_order в плоский список finding_id по приоритету."""

from __future__ import annotations

from agents.verify.group_error.schemas import GroupErrorResult, Finding
from agents.verify.repair_order.schemas import RepairOrderResult


def flatten_ordered_finding_ids(repair_order: RepairOrderResult) -> list[str]:
    """Все ``finding_ids`` в порядке ``ordered_units`` (стабильный порядок внутри unit)."""
    out: list[str] = []
    seen: set[str] = set()
    for unit in sorted(repair_order.ordered_units, key=lambda u: u.position):
        for fid in unit.finding_ids:
            key = str(fid)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out


def pending_finding_ids(
    repair_order: RepairOrderResult,
    *,
    resolved: set[str],
) -> list[str]:
    """Finding ids из flatten минус уже resolved."""
    return [fid for fid in flatten_ordered_finding_ids(repair_order) if fid not in resolved]


def findings_map(analysis: GroupErrorResult | None) -> dict[str, Finding]:
    if analysis is None:
        return {}
    return {f.finding_id: f for f in analysis.findings}


__all__ = [
    "flatten_ordered_finding_ids",
    "pending_finding_ids",
    "findings_map",
]
