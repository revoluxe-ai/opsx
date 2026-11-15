"""Сбор JSON-пакета для Repair Order Agent из ``analysis_result``."""

from __future__ import annotations

import json
from typing import Any

from agents.verify.group_error.schemas import GroupErrorResult


def _stable_input_order(analysis: GroupErrorResult) -> list[dict[str, str]]:
    """Порядок unit id (каждая строка grouped_errors — GROUP)."""
    return [
        {"unit_type": "GROUP", "unit_id": row.error_id}
        for row in analysis.grouped_errors
    ]


def build_repair_order_package(analysis: GroupErrorResult) -> dict[str, Any]:
    """Минимальный handoff для сортировки units (без regroup/diagnose)."""
    findings_minimal = [
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
    ]
    grouped_errors_minimal = [
        {
            "error_id": row.error_id,
            "problem": row.problem,
            "match_basis": row.match_basis,
            "instruments": list(row.instruments),
            "finding_ids": list(row.finding_ids),
            "evidence_refs": list(row.evidence_refs),
        }
        for row in analysis.grouped_errors
    ]
    explicit_links = [
        {
            "finding_id": link.finding_id,
            "related_group_id": link.related_group_id,
            "note": link.note,
        }
        for link in analysis.additional_finding_links
    ]
    return {
        "upstream": {
            "schema_version": analysis.schema_version,
            "agent": analysis.agent,
        },
        "analysis_identity": analysis.analysis_identity.model_dump(mode="json"),
        "input_integrity": analysis.input_integrity.model_dump(mode="json"),
        "grouped_errors": grouped_errors_minimal,
        "additional_finding_links": explicit_links,
        "findings": findings_minimal,
        "triage_summary": analysis.triage_summary.model_dump(mode="json"),
        "sorting_rules": {
            "unit_definition": (
                "Each grouped_errors[] row is one GROUP unit (id=error_id); "
                "instruments lists gate tools that reported this problem."
            ),
            "lexicographic_sort": [
                {
                    "priority": 1,
                    "criterion": "EXPLICIT_DEPENDENCY",
                    "earlier": "Predecessor unit when blocking dependency is explicit in input",
                },
                {
                    "priority": 2,
                    "criterion": "FINDING_DEFINITENESS",
                    "earlier": (
                        "Concrete location (observation path:line), clear repair_workflow/category; "
                        "multi-instrument row with match_basis"
                    ),
                },
                {
                    "priority": 3,
                    "criterion": "POTENTIAL_EFFECT",
                    "earlier": (
                        "Only when input explicitly supports broad impact or severity "
                        "(not finding count alone)"
                    ),
                },
                {
                    "priority": 4,
                    "criterion": "LIMITED_SCOPE",
                    "earlier": "Narrower local scope when tiers 1-3 tie",
                },
                {
                    "priority": 5,
                    "criterion": "STABLE_TIE_BREAK",
                    "earlier": "Preserve input_order below",
                },
            ],
            "input_order": _stable_input_order(analysis),
            "forbidden": [
                "Numeric scoring or weighted priority",
                "Root-cause or causal hypotheses",
                "Regroup findings",
                "Infer dependencies from same file or similar message text alone",
                "Assign executors or repair plans",
                "Run quality gates",
            ],
        },
    }


def package_json(package: dict[str, Any]) -> str:
    """JSON для prompt."""
    return json.dumps(package, ensure_ascii=False, indent=2)
