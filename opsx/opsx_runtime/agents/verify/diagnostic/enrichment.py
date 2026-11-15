"""Сборка ``DiagnosticResult`` из ответа LLM и ``repair_order`` (pipeline, не модель)."""

from __future__ import annotations

from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import RepairOrderResult
from agents.verify.diagnostic.schemas import (
    DiagnosticAnswer,
    DiagnosticResult,
    RemainingQueueUnit,
    RepairUnit,
    source_identity_from_repair_order,
)


def build_remaining_queue(
    repair_order: RepairOrderResult,
    repair_positions: set[int],
) -> list[RemainingQueueUnit]:
    """Остаток очереди: ``ordered_units`` минус units с position из repair unit.

    Args:
        repair_order: Упорядоченные units после repair order hop.
        repair_positions: Множество ``position`` units, включённых в текущий repair unit.

    Returns:
        ``remaining_queue`` с полями из ``repair_order`` (без перенумерации).
    """
    out: list[RemainingQueueUnit] = []
    for unit in repair_order.ordered_units:
        if unit.position in repair_positions:
            continue
        out.append(
            RemainingQueueUnit(
                position=unit.position,
                unit_type=unit.unit_type,
                unit_id=unit.unit_id,
            ),
        )
    return out


def materialize_diagnostic_result(
    answer: DiagnosticAnswer,
    repair_order: RepairOrderResult,
    analysis: GroupErrorResult,
) -> DiagnosticResult:
    """Построить полный ``DiagnosticResult`` из ``DiagnosticAnswer`` и repair_order.

    ``finding_ids`` и ``remaining_queue`` выводятся pipeline; модель задаёт только
    ``repair_unit.ordered_positions`` и текст диагноза.

    Args:
        answer: Распарсенный ответ LLM.
        repair_order: Вход repair order hop.
        analysis: Triage (для совместимости сигнатуры; membership не меняется здесь).

    Returns:
        ``DiagnosticResult`` со status ``DIAGNOSED``.

    Raises:
        ValueError: Неверный anchor, position или пустая очередь repair_order.
    """
    _ = analysis
    if not repair_order.ordered_units:
        raise ValueError("repair_order.ordered_units empty")
    by_position = {u.position: u for u in repair_order.ordered_units}
    head = repair_order.ordered_units[0]
    if answer.anchor_unit_id != head.unit_id:
        raise ValueError(
            f"anchor_unit_id must be first ordered unit {head.unit_id!r}, "
            f"got {answer.anchor_unit_id!r}",
        )
    positions = list(answer.repair_unit.ordered_positions)
    if positions[0] != head.position:
        raise ValueError(
            f"repair_unit.ordered_positions must start with anchor position "
            f"{head.position}, got {positions[0]}",
        )
    unit_ids: list[str] = []
    finding_ids: list[str] = []
    for pos in positions:
        unit = by_position.get(pos)
        if unit is None:
            raise ValueError(f"unknown repair_unit.ordered_positions entry: {pos}")
        unit_ids.append(unit.unit_id)
        finding_ids.extend(unit.finding_ids)

    repair_unit = RepairUnit(
        unit_ids=unit_ids,
        finding_ids=finding_ids,
        affected_areas=list(answer.repair_unit.affected_areas),
        root_cause_summary=answer.repair_unit.root_cause_summary,
        change_boundaries=answer.repair_unit.change_boundaries,
        merged_unit_linkages=list(answer.repair_unit.merged_unit_linkages),
    )
    remaining_queue = build_remaining_queue(repair_order, set(positions))
    return DiagnosticResult(
        status="DIAGNOSED",
        source_identity=source_identity_from_repair_order(repair_order),
        anchor_unit_id=answer.anchor_unit_id,
        repair_unit=repair_unit,
        remaining_queue=remaining_queue,
        pause_reason=None,
    )
