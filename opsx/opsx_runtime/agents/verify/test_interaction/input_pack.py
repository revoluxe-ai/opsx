"""Input pack для test_interaction."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
from pathlib import Path
from typing import Any

from agents.verify.test_triage.input_pack import build_test_triage_input_pack
from workflows.verify.dispatch_cycle.single_repro_probe_key import (
    batch_workers_from_state,
    causal_paths_for_probe,
)


def _file_versions(repo: Path, paths: list[str]) -> dict[str, str]:
    versions: dict[str, str] = {}
    for rel in paths:
        target = resource_path(repo, rel)
        if target.is_file():
            versions[rel] = str(target.stat().st_mtime_ns)
    return versions


def build_test_interaction_input_pack(
    *,
    repository_path: str,
    item: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Контекст finding + single probe + workers/neighbors."""
    base = build_test_triage_input_pack(
        repository_path=repository_path,
        item=item,
        state=state,
    )
    probe = state.get("pytest_single_repro_result")
    if not isinstance(probe, dict):
        probe = {}
    node_id = str(probe.get("node_id") or "")
    repo = Path(repository_path)
    causal_paths = (
        causal_paths_for_probe(repo, node_id=node_id, observation=str(item.get("observation") or ""))
        if node_id
        else []
    )
    return {
        **base,
        "pytest_single_repro_result": probe,
        "batch_workers": batch_workers_from_state(state),
        "causal_path_versions": _file_versions(repo, causal_paths),
        "quality_gate_batch_note": probe.get("batch_conditions_note"),
    }


def pack_json(package: dict[str, Any]) -> str:
    return json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True)
