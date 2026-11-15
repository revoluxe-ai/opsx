"""Сборка dispatcher items и synthetic item для repair группы."""

from __future__ import annotations

from typing import Any

from agents.verify.dispatcher.items import finding_item_from_finding, parse_path_line
from agents.verify.dispatch_unit.schemas import CausalRepairGroup
from agents.verify.group_error.schemas import Finding


def repair_unit_key(finding_ids: list[str]) -> str:
    """Стабильный ключ repair unit для resume и triage result."""
    return "|".join(finding_ids)


def build_repair_unit_queue_item(
    *,
    finding_ids: list[str],
    findings: list[Finding],
    anchor_unit_id: str,
    root_cause_summary: str,
    change_boundaries: str,
    quality_gate_diagnostics: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Один элемент ``dispatcher_pass_queue`` для целой Repair Unit."""
    by_id = {f.finding_id: f for f in findings}
    embedded: list[dict[str, Any]] = []
    for fid in finding_ids:
        finding = by_id.get(fid)
        if finding is not None:
            embedded.append(
                finding_item_from_finding(
                    finding,
                    quality_gate_diagnostics=quality_gate_diagnostics,  # type: ignore[arg-type]
                ),
            )
        else:
            embedded.append(
                {
                    "finding_id": fid,
                    "observation": "",
                    "source_tool": None,
                    "repair_workflow": "WF_UNKNOWN_GATE",
                    "path": None,
                    "line": None,
                },
            )
    return {
        "item_kind": "repair_unit",
        "repair_unit_key": repair_unit_key(finding_ids),
        "anchor_unit_id": anchor_unit_id,
        "finding_ids": list(finding_ids),
        "findings": embedded,
        "root_cause_summary": root_cause_summary,
        "change_boundaries": change_boundaries,
    }


def is_repair_unit_item(item: dict[str, Any] | None) -> bool:
    """True если элемент очереди — repair unit, а не один finding."""
    return isinstance(item, dict) and item.get("item_kind") == "repair_unit"


def finding_item_from_group(
    unit_item: dict[str, Any],
    group: CausalRepairGroup,
) -> dict[str, Any]:
    """Synthetic ``dispatcher_current_item`` для repair hop группы (anchor finding)."""
    embedded = list(unit_item.get("findings") or [])
    by_id = {str(x.get("finding_id")): x for x in embedded if isinstance(x, dict)}
    anchor_id = group.finding_ids[0]
    base = dict(by_id.get(anchor_id) or {"finding_id": anchor_id})
    base["item_kind"] = "causal_group"
    base["repair_unit_key"] = unit_item.get("repair_unit_key")
    base["group_id"] = group.group_id
    base["group_finding_ids"] = list(group.finding_ids)
    base["group_classification"] = group.classification
    base["group_repair_route"] = group.repair_route
    return base


def classify_unit_workflow_mix(unit_item: dict[str, Any]) -> str:
    """``static`` | ``test`` | ``mixed`` по findings repair unit."""
    tools: set[str] = set()
    for raw in unit_item.get("findings") or []:
        if not isinstance(raw, dict):
            continue
        wf = str(raw.get("repair_workflow") or "")
        src = raw.get("source_tool")
        if src == "pytest" or wf.startswith("WF_PYTEST_"):
            tools.add("test")
        elif src in ("pyright", "mypy") or wf.startswith(("WF_PYRIGHT_", "WF_MYPY_")):
            tools.add("static")
        else:
            obs = str(raw.get("observation") or "").lower()
            if "pytest" in obs:
                tools.add("test")
            else:
                tools.add("static")
    if tools == {"test"}:
        return "test"
    if tools == {"static"}:
        return "static"
    return "mixed"
