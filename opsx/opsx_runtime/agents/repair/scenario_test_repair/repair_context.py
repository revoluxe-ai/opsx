"""Собрать frozen mapped binding для prompt repair hop."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.common.scenario_files import scenario_digest, scenario_test_relpath
from agents.repair.scenario_test_repair.mapped_surface import (
    find_reference_test_nodes,
    repair_surfaces,
)
from agents.verify.scenario_coverage_verify.review_semantics import trace_mappings


def binding_for_scenario(repo: Path, change_id: str, scenario_id: str) -> dict[str, Any]:
    mapping = trace_mappings(repo, change_id)
    mapped_node = str(mapping.get(scenario_id) or "")
    mapped_path = mapped_node.split("::", 1)[0] if "::" in mapped_node else ""
    canonical = scenario_test_relpath(change_id, scenario_id)
    peers = find_reference_test_nodes(repo, scenario_id)
    peers = [node for node in peers if node.split("::", 1)[0] != mapped_path]
    primary_reference_peer = next(
        (node for node in peers if "integration" in node.split("::", 1)[0]),
        peers[0] if peers else "",
    )
    return {
        "scenario_id": scenario_id,
        "scenario_digest": scenario_digest(scenario_id),
        "frozen_mapped_test_node": mapped_node,
        "frozen_mapped_test_path": mapped_path,
        "canonical_test_path": canonical,
        "allowed_repair_paths": sorted(repair_surfaces(repo, change_id, scenario_id)),
        "reference_test_nodes": peers[:8],
        "primary_reference_peer": primary_reference_peer,
    }


def build_repair_binding_context(
    repo: Path,
    change_id: str,
    scenario_ids: list[str],
) -> dict[str, Any]:
    bindings = [binding_for_scenario(repo, change_id, sid) for sid in scenario_ids]
    return {"scenario_test_repair_bindings": bindings}
