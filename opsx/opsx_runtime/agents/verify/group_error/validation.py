"""Инварианты ``GroupErrorResult`` после triage (детерминированные проверки)."""

from __future__ import annotations

from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import GroupErrorResult


def recompute_triage_summary(result: GroupErrorResult) -> GroupErrorResult:
    """Синхронизировать ``triage_summary`` с фактическими массивами."""
    from agents.verify.group_error.schemas import TriageSummary

    error_rows = len(result.grouped_errors)
    summary = TriageSummary(
        findings_total=len(result.findings),
        finding_groups_total=error_rows,
        ungrouped_total=0,
    )
    return result.model_copy(update={"triage_summary": summary})


def validate_finding_coverage(result: GroupErrorResult) -> None:
    """Проверить, что каждый finding ID учтён ровно один раз в ``grouped_errors``."""
    expected = {f.finding_id for f in result.findings}
    if not expected:
        return
    seen: set[str] = set()
    duplicates: list[str] = []
    for row in result.grouped_errors:
        for fid in row.finding_ids:
            if fid in seen:
                duplicates.append(fid)
            seen.add(fid)
    if duplicates:
        raise ValueError(
            "finding IDs assigned more than once in grouped_errors: "
            + ", ".join(sorted(set(duplicates))),
        )
    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    if missing or extra:
        raise ValueError(
            "finding coverage mismatch: "
            f"missing={missing[:20]}{'...' if len(missing) > 20 else ''} "
            f"extra={extra[:20]}{'...' if len(extra) > 20 else ''}",
        )


def validate_grouped_errors_instruments(result: GroupErrorResult) -> None:
    """``instruments`` совпадает с union ``source_tool`` по ``finding_ids``."""
    by_id = {f.finding_id: f for f in result.findings}
    for row in result.grouped_errors:
        if not row.finding_ids:
            raise ValueError(f"error {row.error_id}: grouped row requires finding_ids")
        tools_in_group = {
            f.source_tool
            for fid in row.finding_ids
            if (f := by_id.get(fid)) and f.source_tool
        }
        declared = set(row.instruments)
        if not tools_in_group:
            continue
        if declared != tools_in_group:
            raise ValueError(
                f"error {row.error_id}: instruments {sorted(declared)} "
                f"must equal member tools {sorted(tools_in_group)}",
            )


def validate_grouped_errors(result: GroupErrorResult) -> None:
    """Каждая строка ``grouped_errors`` — минимум один finding и один instrument."""
    if not result.findings:
        return
    if not result.grouped_errors:
        raise ValueError("grouped_errors must be non-empty when findings exist")
    by_id = {f.finding_id: f for f in result.findings}
    for row in result.grouped_errors:
        if len(row.finding_ids) < 1:
            raise ValueError(
                f"error {row.error_id}: requires at least 1 finding, "
                f"got {len(row.finding_ids)}",
            )
        tools_in_group = {
            f.source_tool
            for fid in row.finding_ids
            if (f := by_id.get(fid)) and f.source_tool
        }
        if len(tools_in_group) < 1:
            raise ValueError(
                f"error {row.error_id}: requires at least 1 instrument among members, "
                f"finding_ids={row.finding_ids}",
            )


def validate_group_diagnostic_sources(result: GroupErrorResult) -> None:
    """Legacy alias: instruments alignment для нормализованного 1.5."""
    validate_grouped_errors_instruments(result)


def validate_cross_tool_groups(result: GroupErrorResult) -> None:
    """Legacy alias после отказа от обязательного cross-tool."""
    validate_grouped_errors(result)
    validate_grouped_errors_instruments(result)


def validate_group_error_triage_invariants(result: GroupErrorResult) -> None:
    """Проверить partition после canonical normalize (LLM hop / post-materialize)."""
    if not result.findings:
        return
    canon = normalize_group_error_result(result)
    validate_finding_coverage(canon)
    validate_grouped_errors_instruments(canon)
    validate_grouped_errors(canon)
