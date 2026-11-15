"""Детерминированная сборка и упорядочивание processing units для repair_order."""

from __future__ import annotations

from collections.abc import Sequence

from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import OrderedUnit


def build_baseline_ordered_units(analysis: GroupErrorResult) -> list[OrderedUnit]:
    """Собрать units из triage: одна строка ``grouped_errors`` → один GROUP unit.

    Содержимое ``finding_ids`` и типы unit-ов полностью определяются ``analysis_result``;
    LLM не участвует.
    """
    units: list[OrderedUnit] = []
    for position, row in enumerate(analysis.grouped_errors, start=1):
        units.append(
            OrderedUnit(
                position=position,
                unit_type="GROUP",
                unit_id=row.error_id,
                finding_ids=list(row.finding_ids),
                placement_rationale="baseline: grouped_errors order",
                primary_sort_criterion="STABLE_TIE_BREAK",
            ),
        )
    return units


def sort_units_by_ordered_ids(
    units: Sequence[OrderedUnit],
    ordered_unit_ids: Sequence[str],
) -> list[OrderedUnit]:
    """Переставить ``units`` по списку ``ordered_unit_ids`` от модели.

    - ID, которых нет среди ``units``, игнорируются.
    - Unit-ы, не упомянутые моделью, сохраняют относительный порядок и идут **после**
      перечисленных моделью.
    - Текст unit-ов (``finding_ids``, тип) не берётся из ответа модели — только порядок.
    """
    by_id = {unit.unit_id: unit for unit in units}
    known_ids = set(by_id)
    seen: set[str] = set()
    head: list[OrderedUnit] = []
    for unit_id in ordered_unit_ids:
        if unit_id not in known_ids or unit_id in seen:
            continue
        seen.add(unit_id)
        head.append(
            by_id[unit_id].model_copy(
                update={
                    "placement_rationale": "ordered by model unit_id list",
                },
            ),
        )
    tail: list[OrderedUnit] = []
    for unit in units:
        if unit.unit_id in seen:
            continue
        tail.append(
            unit.model_copy(
                update={
                    "placement_rationale": "stable tail: unit_id omitted from model list",
                    "primary_sort_criterion": "STABLE_TIE_BREAK",
                },
            ),
        )
    merged = head + tail
    return [
        unit.model_copy(update={"position": index})
        for index, unit in enumerate(merged, start=1)
    ]
