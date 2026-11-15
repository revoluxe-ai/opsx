"""Нормализация triage к канонической схеме 1.5 (ошибка | instruments)."""

from __future__ import annotations

from agents.verify.group_error.schemas import (
    FindingGroup,
    GateToolId,
    GroupedError,
    GroupErrorResult,
    Finding,
)


def _tools_for_finding_ids(
    finding_ids: list[str],
    by_id: dict[str, Finding],
) -> list[GateToolId]:
    """Уникальные gate-tools по finding_id в стабильном порядке pytest, pyright, mypy."""
    order: list[GateToolId] = ["pytest", "pyright", "mypy"]
    seen: set[GateToolId] = set()
    for fid in finding_ids:
        row = by_id.get(fid)
        if row is None or row.source_tool is None:
            continue
        seen.add(row.source_tool)
    return [tool for tool in order if tool in seen]


def _problem_stub(finding: Finding | None, *, finding_id: str) -> str:
    if finding is None:
        return finding_id
    obs = str(finding.observation or "").strip()
    if obs:
        return obs[:500]
    return finding_id


def _legacy_group_to_grouped(
    group: FindingGroup,
    by_id: dict[str, Finding],
) -> GroupedError:
    instruments = _tools_for_finding_ids(list(group.finding_ids), by_id)
    declared = list(group.diagnostic_sources)
    if declared and instruments:
        merged = [t for t in declared if t in instruments]
        if merged:
            instruments = merged
    return GroupedError(
        error_id=group.group_id,
        problem=group.matched_problem,
        match_basis=group.match_basis,
        instruments=instruments,
        finding_ids=list(group.finding_ids),
        evidence_refs=list(group.evidence_refs),
        linked_evidence=list(group.linked_evidence),
        match_evidence=list(group.match_evidence),
    )


def _sync_grouped_instruments(
    row: GroupedError,
    by_id: dict[str, Finding],
) -> GroupedError:
    """``instruments`` должен совпадать с union ``source_tool`` членов группы."""
    computed = _tools_for_finding_ids(list(row.finding_ids), by_id)
    if computed:
        return row.model_copy(update={"instruments": computed})
    return row


def normalize_group_error_result(
    result: GroupErrorResult,
    *,
    fill_coverage_gaps: bool = True,
) -> GroupErrorResult:
    """Привести 1.4/частичный 1.5 ответ к ``schema_version=1.5`` и ``grouped_errors`` only.

    Args:
        result: Распарсенный triage (в т.ч. с legacy ``finding_groups`` / ``ungrouped``).
        fill_coverage_gaps: Добавить singleton-строки для findings без членства (group_error hop).

    Returns:
        Копия с заполненным ``grouped_errors``, пустыми legacy-полями.
    """
    by_id = {f.finding_id: f for f in result.findings}
    grouped: list[GroupedError] = []
    covered: set[str] = set()

    for row in result.grouped_errors:
        synced = _sync_grouped_instruments(row, by_id)
        grouped.append(synced)
        covered.update(synced.finding_ids)

    for legacy in result.finding_groups:
        converted = _legacy_group_to_grouped(legacy, by_id)
        synced = _sync_grouped_instruments(converted, by_id)
        grouped.append(synced)
        covered.update(synced.finding_ids)

    for fid in result.ungrouped_finding_ids:
        if fid in covered:
            continue
        finding = by_id.get(fid)
        instruments = _tools_for_finding_ids([fid], by_id)
        grouped.append(
            GroupedError(
                error_id=fid,
                problem=_problem_stub(finding, finding_id=fid),
                match_basis="singleton finding",
                instruments=instruments,
                finding_ids=[fid],
            ),
        )
        covered.add(fid)

    if fill_coverage_gaps:
        for finding in result.findings:
            if finding.finding_id in covered:
                continue
            instruments = _tools_for_finding_ids([finding.finding_id], by_id)
            grouped.append(
                GroupedError(
                    error_id=finding.finding_id,
                    problem=_problem_stub(finding, finding_id=finding.finding_id),
                    match_basis="coverage fallback",
                    instruments=instruments,
                    finding_ids=[finding.finding_id],
                ),
            )
            covered.add(finding.finding_id)

    return result.model_copy(
        update={
            "schema_version": "1.5",
            "grouped_errors": grouped,
            "finding_groups": [],
            "ungrouped_finding_ids": [],
        },
    )
