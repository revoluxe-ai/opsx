"""Bounded OPSX/verify контекст для Static Triage (без dump всего репозитория)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.state import GraphState
from agents.verify.diagnostic.schemas import DiagnosticResult
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import RepairOrderResult
from workflows.verify.gate_repair.pytest_failure_excerpt import lookup_pytest_failure_excerpt


def _finding_from_analysis(
    analysis: GroupErrorResult | None,
    finding_id: str,
) -> dict[str, Any] | None:
    if analysis is None:
        return None
    for finding in analysis.findings:
        if finding.finding_id == finding_id:
            return finding.model_dump(mode="json")
    return None


def _group_snippet_for_finding(
    analysis: GroupErrorResult | None,
    finding_id: str,
) -> dict[str, Any] | None:
    if analysis is None:
        return None
    for row in analysis.grouped_errors:
        if finding_id in row.finding_ids:
            return {
                "error_id": row.error_id,
                "problem": row.problem,
                "match_basis": row.match_basis,
                "instruments": list(row.instruments),
                "finding_ids": list(row.finding_ids),
            }
    return None


def _repair_order_snippet(
    repair_order: RepairOrderResult | None,
    finding_id: str,
) -> dict[str, Any] | None:
    if repair_order is None:
        return None
    for unit in repair_order.ordered_units:
        if finding_id in unit.finding_ids:
            return {
                "position": unit.position,
                "unit_id": unit.unit_id,
                "unit_type": unit.unit_type,
                "finding_ids": list(unit.finding_ids),
                "placement_rationale": unit.placement_rationale,
            }
    return None


def _diagnostic_snippet(
    diagnostic: DiagnosticResult | None,
    finding_id: str,
) -> dict[str, Any] | None:
    if diagnostic is None or diagnostic.status != "DIAGNOSED":
        return None
    repair = diagnostic.repair_unit
    if repair is None or finding_id not in repair.finding_ids:
        return {
            "status": diagnostic.status,
            "anchor_unit_id": diagnostic.anchor_unit_id,
            "in_current_repair_unit": False,
        }
    return {
        "status": diagnostic.status,
        "anchor_unit_id": diagnostic.anchor_unit_id,
        "in_current_repair_unit": True,
        "repair_unit": {
            "unit_ids": list(repair.unit_ids),
            "finding_ids": list(repair.finding_ids),
            "root_cause_summary": repair.root_cause_summary,
            "change_boundaries": repair.change_boundaries,
            "affected_areas": list(repair.affected_areas),
        },
    }


def _gate_issue_snippet(
    diagnostics: dict[str, Any] | None,
    *,
    source_tool: str | None,
    observation: str,
) -> str | None:
    if not diagnostics or not source_tool:
        return None
    issues = diagnostics.get(source_tool)
    if not isinstance(issues, list):
        return None
    obs = observation.strip()
    for raw in issues:
        text = str(raw).strip()
        if not text:
            continue
        if obs and (text == obs or obs in text or text in obs):
            return text[:2000]
    if issues:
        first = str(issues[0]).strip()
        return first[:2000] if first else None
    return None


def _verify_run_artifact_hints(state: GraphState, change_id: str) -> dict[str, Any]:
    """Снимок наличия verify-сниппетов в state (без путей к ``opsx/receipts/**``)."""
    _ = change_id
    hints: dict[str, Any] = {
        "expected_artifacts": [
            "analysis_result.json",
            "repair_order_result.json",
            "diagnostic_result.json",
            "quality_gate_diagnostics.json",
        ],
        "present_in_state": {
            "analysis_result": state.get("analysis_result") is not None,
            "repair_order_result": state.get("repair_order_result") is not None,
            "diagnostic_result": state.get("diagnostic_result") is not None,
            "quality_gate_diagnostics": state.get("quality_gate_diagnostics") is not None,
        },
    }
    hints["present_artifact_names"] = [
        f"{key}.json"
        for key, ok in hints["present_in_state"].items()
        if ok
    ]
    return hints


def build_bounded_opsx_context(
    state: GraphState,
    *,
    item: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Собрать узкий контекст verify-run для triage/repair prompt.

    Returns:
        (context_dict, manifest) — manifest перечисляет включённые секции.
    """
    finding_id = str(item.get("finding_id") or "")
    change_id = str(state.get("change_id") or "").strip()

    analysis: GroupErrorResult | None = None
    raw_analysis = state.get("analysis_result")
    if isinstance(raw_analysis, dict):
        analysis = GroupErrorResult.model_validate(raw_analysis)

    repair_order: RepairOrderResult | None = None
    raw_ro = state.get("repair_order_result")
    if isinstance(raw_ro, dict):
        repair_order = RepairOrderResult.model_validate(raw_ro)

    diagnostic: DiagnosticResult | None = None
    raw_diag = state.get("diagnostic_result")
    if isinstance(raw_diag, dict):
        diagnostic = DiagnosticResult.model_validate(raw_diag)

    diagnostics_raw = state.get("quality_gate_diagnostics")
    diagnostics = diagnostics_raw if isinstance(diagnostics_raw, dict) else None

    context: dict[str, Any] = {}
    manifest: list[str] = []

    if change_id:
        context["verify_run"] = _verify_run_artifact_hints(state, change_id)
        manifest.append("verify_run.artifact_hints_for_change")

    snippet = _gate_issue_snippet(
        diagnostics,
        source_tool=str(item.get("source_tool") or "") or None,
        observation=str(item.get("observation") or ""),
    )
    if snippet is not None:
        context["quality_gate_issue_snippet"] = snippet
        manifest.append("quality_gate_diagnostics.single_issue_snippet")

    if str(item.get("source_tool") or "") == "pytest":
        excerpt = item.get("pytest_failure_excerpt")
        if not isinstance(excerpt, str) or not excerpt.strip():
            excerpt = lookup_pytest_failure_excerpt(
                diagnostics,
                observation=str(item.get("observation") or ""),
            )
        if isinstance(excerpt, str) and excerpt.strip():
            context["pytest_failure_excerpt"] = excerpt
            manifest.append("quality_gate_diagnostics.pytest_failure_excerpt")

    finding_blob = _finding_from_analysis(analysis, finding_id)
    if finding_blob is not None:
        context["group_error_finding"] = finding_blob
        manifest.append("group_error.finding_by_id")

    group_blob = _group_snippet_for_finding(analysis, finding_id)
    if group_blob is not None:
        context["group_error_group"] = group_blob
        manifest.append("group_error.group_or_ungrouped_snippet")

    ro_blob = _repair_order_snippet(repair_order, finding_id)
    if ro_blob is not None:
        context["repair_order_unit"] = ro_blob
        manifest.append("repair_order.unit_containing_finding")

    diag_blob = _diagnostic_snippet(diagnostic, finding_id)
    if diag_blob is not None:
        context["diagnostic"] = diag_blob
        manifest.append("diagnostic.repair_unit_or_anchor_snippet")

    if isinstance(state.get("static_triage_result"), dict):
        context["prior_static_triage_on_state"] = {
            "note": "ignored for first pass; reserved for replay/debug",
        }

    return context, manifest
