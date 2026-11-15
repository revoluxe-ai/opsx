"""Узел построения списка slice-объектов из APPLY-DESIGN."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.state import GraphState
from workflows.implementation.receipt_loader import load_apply_design
from workflows.implementation.routing_contract import (
    RUN_STATUS_RUNNING,
    STAGE_APPLY_SCAFFOLD,
)
from workflows.repo_paths import resolve_opsx_repository_root


def slice_ids_from_design(design: dict[str, Any]) -> list[str]:
    """Извлечь упорядоченные slice_id из APPLY-DESIGN."""
    order = design.get("slice_order")
    if isinstance(order, dict):
        nodes = order.get("nodes")
        if isinstance(nodes, list) and nodes:
            return [str(x) for x in nodes]
    slices = design.get("slices")
    if isinstance(slices, list):
        out: list[str] = []
        for row in slices:
            if isinstance(row, dict) and row.get("slice_id"):
                out.append(str(row["slice_id"]))
        if out:
            return out
    return []


async def implementation_slice_list_node(state: GraphState) -> dict[str, Any]:
    """Собрать ``implementation_slices`` и начать с первого слайса."""
    change_id = str(state.get("change_id") or "").strip()
    if not change_id:
        raise ValueError("change_id is required for slice list")
    repo_root = resolve_opsx_repository_root(Path(str(state["repository_path"])))
    design = load_apply_design(change_id, repo_root=repo_root)
    if design is None:
        return {
            "implementation_status": "FAILED",
            "implementation_recovery_code": "APPLY_DESIGN_MISSING",
            "error": "APPLY-DESIGN.json missing after design lane",
        }
    ids = slice_ids_from_design(design)
    slice_objects = [{"slice_id": sid} for sid in ids]
    first = ids[0] if ids else None
    return {
        "implementation_slices": slice_objects,
        "implementation_slice_index": 0,
        "implementation_slice_id": first,
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_stage": STAGE_APPLY_SCAFFOLD,
        "apply_design_lane_complete": True,
    }
