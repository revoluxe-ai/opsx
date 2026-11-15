"""Harness-first ordering в repair_order."""

from agents.verify.repair_order.harness_priority import prioritize_harness_units
from agents.verify.repair_order.schemas import FindingWorkflowRef, OrderedUnit


def test_harness_unit_moves_to_front() -> None:
    u1 = OrderedUnit(
        position=1,
        unit_type="GROUP",
        unit_id="G1",
        finding_ids=["F-0001"],
        finding_workflows=[
            FindingWorkflowRef(
                finding_id="F-0001",
                source_tool="pytest",
                repair_workflow="WF_PYTEST_FAILURE",
            ),
        ],
        placement_rationale="a",
    )
    u2 = OrderedUnit(
        position=2,
        unit_type="GROUP",
        unit_id="G2",
        finding_ids=["F-0002"],
        finding_workflows=[
            FindingWorkflowRef(
                finding_id="F-0002",
                source_tool="pytest",
                repair_workflow="WF_PYTEST_COLLECTION",
            ),
        ],
        placement_rationale="b",
    )
    ordered = prioritize_harness_units([u1, u2])
    assert ordered[0].unit_id == "G2"
    assert ordered[0].position == 1
