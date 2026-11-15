"""Тесты flatten repair_order в finding_id."""

from __future__ import annotations

from agents.verify.dispatcher.flatten_order import (
    flatten_ordered_finding_ids,
    pending_finding_ids,
)
from agents.verify.repair_order.schemas import OrderedUnit, RepairOrderResult, SourceIdentity


def _ro() -> RepairOrderResult:
    return RepairOrderResult(
        status="SORTED",
        source_identity=SourceIdentity(change_id="c1", identity_status="PARTIAL"),
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="GROUP",
                unit_id="E-1",
                finding_ids=["F-0001", "F-0002"],
                placement_rationale="a",
            ),
            OrderedUnit(
                position=2,
                unit_type="UNGROUPED",
                unit_id="F-0003",
                finding_ids=["F-0003"],
                placement_rationale="b",
            ),
        ],
    )


def test_flatten_order():
    assert flatten_ordered_finding_ids(_ro()) == ["F-0001", "F-0002", "F-0003"]


def test_pending_excludes_resolved():
    assert pending_finding_ids(_ro(), resolved={"F-0001"}) == ["F-0002", "F-0003"]
