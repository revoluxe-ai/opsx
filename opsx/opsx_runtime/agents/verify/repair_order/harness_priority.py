"""Harness-first ordering после LLM sort."""

from __future__ import annotations

from agents.verify.pytest_run_health import is_harness_blocker
from agents.verify.repair_order.schemas import OrderedUnit


def prioritize_harness_units(units: list[OrderedUnit]) -> list[OrderedUnit]:
    """Поднять units с ``WF_PYTEST_COLLECTION`` в начало очереди."""
    harness: list[OrderedUnit] = []
    rest: list[OrderedUnit] = []
    for unit in units:
        if any(is_harness_blocker(ref.repair_workflow) for ref in unit.finding_workflows):
            harness.append(unit)
        else:
            rest.append(unit)
    merged = harness + rest
    return [
        unit.model_copy(update={"position": idx})
        for idx, unit in enumerate(merged, start=1)
    ]
