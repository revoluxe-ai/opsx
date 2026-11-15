"""Детерминированное разбиение Repair Unit на причинные группы без LLM.

Используется, когда Diagnostic объединил findings, а evidence показывает
независимые lane (static vs pytest) или разные классы исключений.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from agents.verify.dispatch_unit.queue_items import classify_unit_workflow_mix
from agents.verify.dispatch_unit.schemas import CausalRepairGroup

_ExceptionToken = re.compile(
    r"\b([A-Z][A-Za-z]+(?:Error|Exception))\b",
)
_TestFailMark = re.compile(r"\b(FAILED|ERROR)\b", re.IGNORECASE)


def _finding_rows(unit_item: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Упорядоченные (finding_id, embedded dict) для unit."""
    by_id = {
        str(x.get("finding_id")): x
        for x in (unit_item.get("findings") or [])
        if isinstance(x, dict) and x.get("finding_id")
    }
    rows: list[tuple[str, dict[str, Any]]] = []
    for fid in unit_item.get("finding_ids") or []:
        key = str(fid)
        row = by_id.get(key) or {"finding_id": key, "observation": ""}
        rows.append((key, row))
    return rows


def primary_exception_token(observation: str) -> str | None:
    """Грубый «отпечаток» доминирующего исключения в observation."""
    text = (observation or "").strip()
    if not text:
        return None
    matches = _ExceptionToken.findall(text)
    if matches:
        return matches[-1]
    if _TestFailMark.search(text):
        return "PytestFailure"
    return None


def infer_finding_repair_lane(
    finding: dict[str, Any],
) -> tuple[Literal["LOGIC", "TYPING", "TEST"], Literal["static", "test", "test_harness"]]:
    """Классификация и repair_route для одного finding по gate metadata."""
    wf = str(finding.get("repair_workflow") or "")
    src = str(finding.get("source_tool") or "")
    obs = str(finding.get("observation") or "").lower()
    if src == "pytest" or wf.startswith("WF_PYTEST_") or "pytest" in obs:
        if "harness" in wf.lower() or "fixture" in obs:
            return "TEST", "test_harness"
        return "TEST", "test"
    if src in ("pyright", "mypy") or wf.startswith(("WF_PYRIGHT_", "WF_MYPY_")):
        return "TYPING", "static"
    if "pyright" in obs or "mypy" in obs:
        return "TYPING", "static"
    return "LOGIC", "static"


def _singleton_group(
    *,
    finding_id: str,
    finding: dict[str, Any],
    rationale: str,
) -> CausalRepairGroup:
    classification, repair_route = infer_finding_repair_lane(finding)
    return CausalRepairGroup(
        group_id=f"G-{finding_id}",
        finding_ids=[finding_id],
        classification=classification,
        repair_route=repair_route,
        rationale=rationale,
    )


def try_heuristic_causal_groups(unit_item: dict[str, Any]) -> list[CausalRepairGroup] | None:
    """Вернуть partition групп без LLM или None, если эвристика не применима.

    Args:
        unit_item: Элемент ``item_kind=repair_unit`` из dispatcher queue.

    Returns:
        Список групп, покрывающий все ``finding_ids``, либо None для LLM triage.
    """
    rows = _finding_rows(unit_item)
    if len(rows) < 2:
        return None

    mix = classify_unit_workflow_mix(unit_item)
    if mix == "mixed":
        return [
            _singleton_group(
                finding_id=fid,
                finding=row,
                rationale=(
                    "Diagnostic unit mixes static and pytest findings; "
                    "split per finding with explicit repair_route"
                ),
            )
            for fid, row in rows
        ]

    tokens = [primary_exception_token(str(row.get("observation") or "")) for _, row in rows]
    if all(tokens) and len(set(tokens)) == len(tokens):
        return [
            _singleton_group(
                finding_id=fid,
                finding=row,
                rationale=(
                    f"Independent exception signatures in unit ({tokens[i]}); "
                    "split instead of whole-unit PAUSE"
                ),
            )
            for i, (fid, row) in enumerate(rows)
        ]

    return None
