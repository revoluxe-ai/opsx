"""Сжатие analysis/repair_order после успешного environment_prep."""

from __future__ import annotations

from agents.state import GraphState
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.group_error.validation import recompute_triage_summary


def resolved_finding_ids_from_state(state: GraphState) -> set[str]:
    raw = state.get("environment_prep_resolved_finding_ids")
    if not isinstance(raw, list):
        return set()
    return {str(x).strip() for x in raw if str(x).strip()}


def shrink_group_error_for_resolved(
    analysis: GroupErrorResult,
    resolved_ids: set[str],
) -> GroupErrorResult:
    """Убрать findings, закрытые environment_prep, из handoff."""
    if not resolved_ids:
        return analysis
    findings = [f for f in analysis.findings if f.finding_id not in resolved_ids]
    grouped = [
        row.model_copy(
            update={
                "finding_ids": [fid for fid in row.finding_ids if fid not in resolved_ids],
            },
        )
        for row in analysis.grouped_errors
    ]
    grouped = [row for row in grouped if row.finding_ids]
    ungrouped = [fid for fid in analysis.ungrouped_finding_ids if fid not in resolved_ids]
    shrunk = analysis.model_copy(
        update={
            "findings": findings,
            "grouped_errors": grouped,
            "ungrouped_finding_ids": ungrouped,
        },
    )
    return recompute_triage_summary(shrunk)


def cohort_finding_ids_for_resolution(
    *,
    cohorts_addressed: list[str],
    cohorts_pack: dict,
    collect_ok: bool,
) -> list[str]:
    """Finding IDs когорт, которые можно снять с очереди после collect_ok."""
    if not collect_ok or not cohorts_addressed:
        return []
    raw_cohorts = cohorts_pack.get("cohorts")
    if not isinstance(raw_cohorts, list):
        return []
    addressed = set(cohorts_addressed)
    resolved: list[str] = []
    for row in raw_cohorts:
        if not isinstance(row, dict):
            continue
        if str(row.get("cohort_id") or "") not in addressed:
            continue
        ids = row.get("finding_ids")
        if isinstance(ids, list):
            resolved.extend(str(x) for x in ids if x)
    return resolved
