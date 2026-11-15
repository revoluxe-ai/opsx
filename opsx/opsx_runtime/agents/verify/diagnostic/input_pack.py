"""Сбор JSON-пакета для Diagnostic Agent из repair_order + analysis."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import RepairOrderResult
from workflows.artifact_paths import receipts_dir

_DIAGNOSTIC_READ_TOOLS = frozenset({"read", "grep", "glob", "ls"})


def diagnostic_findings_artifact_path(
    *,
    repository_path: str,
    change_id: str,
    stream_log: str | None = None,
) -> Path:
    """Путь JSON-файла с полными findings/units для diagnostic hop.

    Файл лежит в ``opsx/receipts/<change_id>/``.

    Args:
        repository_path: Корень репозитория.
        change_id: OpenSpec change id.
        stream_log: Путь run-log (stem задаёт имя артефакта для legacy ndjson).

    Returns:
        Абсолютный путь ``…/opsx/receipts/<change_id>/<stem>-findings.json``.
    """
    repo = Path(repository_path)
    out_dir = receipts_dir(repo, change_id)
    stem = "diagnostic-input"
    if stream_log and str(stream_log).strip():
        log_name = Path(stream_log).name
        if log_name.endswith(".live.ndjson"):
            stem = log_name[: -len(".live.ndjson")]
        elif log_name:
            stem = Path(log_name).stem
    return out_dir / f"{stem}-findings.json"


def build_diagnostic_findings_payload(
    repair_order: RepairOrderResult,
    analysis: GroupErrorResult,
) -> dict[str, Any]:
    """Полный findings/units payload для prompt и machine artifact."""
    findings_by_id = {f.finding_id: f for f in analysis.findings}

    def _finding_rows(finding_ids: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for fid in finding_ids:
            finding = findings_by_id.get(fid)
            if finding is None:
                rows.append({"finding_id": fid, "missing_from_analysis": True})
                continue
            rows.append(
                {
                    "finding_id": finding.finding_id,
                    "source_tool": finding.source_tool,
                    "repair_workflow": finding.repair_workflow,
                    "category": finding.category,
                    "observation": finding.observation,
                    "scope": finding.scope,
                    "evidence_refs": finding.evidence_refs,
                    "causal_group_id": finding.causal_group_id,
                },
            )
        return rows

    units_payload = [
        {
            "position": u.position,
            "unit_type": u.unit_type,
            "unit_id": u.unit_id,
            "finding_ids": list(u.finding_ids),
            "finding_workflows": [fw.model_dump(mode="json") for fw in u.finding_workflows],
            "primary_sort_criterion": u.primary_sort_criterion,
            "placement_rationale": u.placement_rationale,
            "findings": _finding_rows(list(u.finding_ids)),
        }
        for u in repair_order.ordered_units
    ]
    return {
        "schema_version": "1.0",
        "agent": "diagnostic_findings_artifact",
        "upstream": {
            "repair_order_schema_version": repair_order.schema_version,
            "group_error_schema_version": analysis.schema_version,
        },
        "source_identity": repair_order.source_identity.model_dump(mode="json"),
        "ordered_units": units_payload,
        "findings": [
            {
                "finding_id": f.finding_id,
                "source_tool": f.source_tool,
                "repair_workflow": f.repair_workflow,
                "category": f.category,
                "observation": f.observation,
                "scope": f.scope,
                "evidence_refs": f.evidence_refs,
                "causal_group_id": f.causal_group_id,
            }
            for f in analysis.findings
        ],
        "grouped_errors": [
            {
                "error_id": row.error_id,
                "problem": row.problem,
                "match_basis": row.match_basis,
                "instruments": list(row.instruments),
                "finding_ids": list(row.finding_ids),
            }
            for row in analysis.grouped_errors
        ],
        "confirmed_dependencies": [
            d.model_dump(mode="json") for d in repair_order.confirmed_dependencies
        ],
    }


def write_diagnostic_findings_artifact(
    *,
    repository_path: str,
    change_id: str,
    repair_order: RepairOrderResult,
    analysis: GroupErrorResult,
    stream_log: str | None = None,
) -> Path:
    """Записать полный список ошибок/findings на диск (для machine/log, не для модели)."""
    path = diagnostic_findings_artifact_path(
        repository_path=repository_path,
        change_id=change_id,
        stream_log=stream_log,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_diagnostic_findings_payload(repair_order, analysis)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def repo_relative_path(repository_path: str, absolute_path: Path) -> str:
    """Repo-relative путь для prompt (fallback — absolute)."""
    repo = Path(repository_path)
    try:
        return str(absolute_path.resolve().relative_to(repo.resolve()))
    except ValueError:
        return str(absolute_path)


def build_diagnostic_package(
    repair_order: RepairOrderResult,
    analysis: GroupErrorResult,
    *,
    findings_embed: dict[str, Any],
) -> dict[str, Any]:
    """Компактный handoff: индекс units + встроенные findings (без путей к receipts)."""
    ordered_index = [
        {
            "position": u.position,
            "unit_type": u.unit_type,
            "unit_id": u.unit_id,
            "finding_ids": list(u.finding_ids),
            "primary_sort_criterion": u.primary_sort_criterion,
        }
        for u in repair_order.ordered_units
    ]
    anchor = ordered_index[0] if ordered_index else None
    tail = ordered_index[1:] if len(ordered_index) > 1 else []

    return {
        "upstream": {
            "repair_order_schema_version": repair_order.schema_version,
            "group_error_schema_version": analysis.schema_version,
        },
        "source_identity": repair_order.source_identity.model_dump(mode="json"),
        "confirmed_dependencies": [
            d.model_dump(mode="json") for d in repair_order.confirmed_dependencies
        ],
        "findings_embed": findings_embed,
        "ordered_units_index": ordered_index,
        "anchor": anchor,
        "remaining_ordered_units_index": tail,
        "diagnostic_rules": {
            "anchor_selection": "First entry in repair_order.ordered_units (lowest position).",
            "findings_source": (
                "Full observation/scope/evidence for every finding_id is in "
                "findings_embed above — do not open files or grep the repo."
            ),
            "output_repair_unit": (
                "In repair_unit.ordered_positions list only position integers from "
                "ordered_units_index for units included in this repair unit "
                "(first = anchor position). Pipeline builds unit_ids, finding_ids, "
                "and remaining_queue."
            ),
            "merge_policy": (
                "Include additional positions only with confirmed shared root cause "
                "or same coordinated change; never merge on same file or batch convenience."
            ),
            "affected_areas": [
                "typing",
                "application_code",
                "tests",
                "configuration",
            ],
            "forbidden": [
                "Regroup or split triage/repair_order units",
                "Merge without explicit causal linkage in output",
                "Assign executors or run gates",
                "Diagnose entire queue in one hop beyond confirmed linkage",
                "Change repair_order sort order",
                "Echo remaining_queue or finding observation text in JSON output",
            ],
        },
    }


def package_json(package: dict[str, Any]) -> str:
    """JSON для prompt."""
    return json.dumps(package, ensure_ascii=False, indent=2)
