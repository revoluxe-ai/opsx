"""Чтение проекций журнала без повторного прогона verify."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            rows.append(item)
    return rows


def events_for_change(repo_root: Path, change_id: str) -> list[dict[str, Any]]:
    """События одного change из шарда (без глобального файла)."""
    path = resource_path(repo_root, "opsx", "runs", "bug-log", "by-change", change_id, "events.ndjson")
    return _read(path)


def timeline_for_bug(repo_root: Path, bug_id: str) -> list[dict[str, Any]]:
    """Полный timeline бага."""
    path = resource_path(repo_root, "opsx", "runs", "bug-log", "by-bug", bug_id, "timeline.ndjson")
    return _read(path)


def load_manifest(repo_root: Path) -> dict[str, Any]:
    """ANALYSIS_MANIFEST.json или пустой объект."""
    path = resource_path(repo_root, "opsx", "runs", "bug-log", "ANALYSIS_MANIFEST.json")
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
