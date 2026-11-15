"""Typed rebind test_trace_matrix.json на сильнейший peer (port-first fallback)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agents.repair.scenario_test_repair.mapped_surface import (
    find_reference_test_nodes,
    mapped_body_is_weak,
)
from agents.verify.scenario_coverage_verify.review_semantics import trace_mappings

_LOGGER = logging.getLogger(__name__)


def rebind_trace_to_strongest_peer(
    repo: Path,
    change_id: str,
    scenario_id: str,
) -> dict[str, Any] | None:
    """Обновить matrix entry, если mapped stub и ровно один сильный peer node."""
    if not mapped_body_is_weak(repo, change_id, scenario_id):
        return None
    mapped_node = str(trace_mappings(repo, change_id).get(scenario_id) or "")
    mapped_path = mapped_node.split("::", 1)[0] if "::" in mapped_node else ""
    peers = [
        node for node in find_reference_test_nodes(repo, scenario_id)
        if node != mapped_node and node.split("::", 1)[0] != mapped_path
    ]
    if len(peers) != 1:
        return None
    new_node = peers[0]
    from workflows.artifact_paths import scenario_machine_receipt_path

    matrix_path = scenario_machine_receipt_path(repo, change_id, "test_trace_matrix.json")
    if not matrix_path.is_file():
        return None
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return None
    rebound = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("scenario_id") == scenario_id:
            entry["test_node_id"] = new_node
            rebound = True
            break
    if not rebound:
        return None
    matrix_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _LOGGER.info(
        "matrix_rebind scenario_id=%s old=%s new=%s",
        scenario_id,
        mapped_node,
        new_node,
    )
    return {
        "matrix_rebound": True,
        "scenario_id": scenario_id,
        "previous_test_node": mapped_node,
        "test_node_id": new_node,
        "matrix_path": matrix_path.relative_to(repo).as_posix(),
    }
