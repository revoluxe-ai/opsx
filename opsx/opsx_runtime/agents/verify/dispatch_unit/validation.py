"""Инварианты разбиения Repair Unit на причинные группы."""

from __future__ import annotations

from agents.verify.dispatch_unit.schemas import CausalRepairGroup, RepairUnitTriageResult


def validate_repair_unit_triage_partition(
    result: RepairUnitTriageResult,
    *,
    expected_finding_ids: list[str],
) -> None:
    """Каждый finding ровно в одной группе; нет лишних/пропущенных id.

    Args:
        result: Ответ группового triage.
        expected_finding_ids: ``repair_unit.finding_ids`` из diagnostic.

    Raises:
        ValueError: PAUSE без reason, дубликаты, пропуски или лишние id.
    """
    if result.status == "PAUSE":
        if not (result.pause_reason or "").strip():
            raise ValueError("PAUSE requires pause_reason")
        return
    if not result.causal_groups:
        raise ValueError("GROUPED requires at least one causal_groups entry")
    expected = list(expected_finding_ids)
    seen: list[str] = []
    for group in result.causal_groups:
        if not group.finding_ids:
            raise ValueError(f"group {group.group_id!r} has empty finding_ids")
        if group.classification == "UNCLEAR":
            raise ValueError(
                f"group {group.group_id!r} has UNCLEAR classification; use PAUSE instead",
            )
        seen.extend(group.finding_ids)
    if sorted(seen) != sorted(expected):
        raise ValueError(
            f"causal_groups must partition finding_ids exactly; "
            f"expected {sorted(expected)}, got {sorted(seen)}",
        )
    if len(seen) != len(set(seen)):
        dupes = sorted({fid for fid in seen if seen.count(fid) > 1})
        raise ValueError(f"duplicate finding_id across groups: {dupes}")


def groups_from_triage_dict(raw: dict) -> list[CausalRepairGroup]:
    """Преобразовать JSON triage в список ``CausalRepairGroup``."""
    result = RepairUnitTriageResult.model_validate(raw)
    return list(result.causal_groups)
