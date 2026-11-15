"""Детерминированное заполнение ``finding_workflows`` на ``OrderedUnit``."""

from __future__ import annotations

from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import FindingWorkflowRef, OrderedUnit, RepairOrderResult


def workflow_map(analysis: GroupErrorResult) -> dict[str, FindingWorkflowRef]:
    """Индекс finding_id → workflow ref из upstream triage."""
    out: dict[str, FindingWorkflowRef] = {}
    for f in analysis.findings:
        out[f.finding_id] = FindingWorkflowRef(
            finding_id=f.finding_id,
            source_tool=f.source_tool,
            repair_workflow=f.repair_workflow,
            category=f.category,
        )
    return out


def attach_finding_workflows(
    result: RepairOrderResult,
    analysis: GroupErrorResult,
) -> RepairOrderResult:
    """Pipeline: проставить ``finding_workflows`` на каждой unit из ``analysis.findings``."""
    by_id = workflow_map(analysis)
    units: list[OrderedUnit] = []
    for unit in result.ordered_units:
        refs = [
            by_id[fid]
            for fid in unit.finding_ids
            if fid in by_id
        ]
        units.append(unit.model_copy(update={"finding_workflows": refs}))
    return result.model_copy(update={"ordered_units": units})
