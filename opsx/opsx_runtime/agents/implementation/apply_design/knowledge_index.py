"""Knowledge index прошлых APPLY-DESIGN без обхода opsx/receipts моделью."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
from pathlib import Path
from typing import Mapping

from agents.capabilities.manifest import PriorDesignDescriptor
from workflows.artifact_paths import apply_design_artifact_path


def prior_design_index(repo_root: Path) -> Mapping[str, PriorDesignDescriptor]:
    """Собрать ``prior_design:<change_id>`` для существующих APPLY-DESIGN.json."""
    root = repo_root.resolve()
    out: dict[str, PriorDesignDescriptor] = {}
    base = resource_path(root, "openspec", "changes")
    if not base.is_dir():
        return {}
    for change_dir in sorted(base.iterdir()):
        if not change_dir.is_dir():
            continue
        change_id = change_dir.name
        if change_id == "archive":
            continue
        path = apply_design_artifact_path(root, change_id)
        if not path.is_file():
            continue
        summary = _brief_summary(path)
        sid = f"prior_design:{change_id}"
        out[sid] = PriorDesignDescriptor(
            source_id=sid,
            change_id=change_id,
            summary=summary,
        )
    return out


def _brief_summary(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return "(unreadable APPLY-DESIGN)"
    if not isinstance(data, dict):
        return "(invalid APPLY-DESIGN)"
    status = str(data.get("status") or "")
    slices = data.get("slices")
    n_slices = len(slices) if isinstance(slices, list) else 0
    return f"status={status}, slices={n_slices}"
