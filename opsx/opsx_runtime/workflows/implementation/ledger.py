"""Durable ledger ``<changeRoot>/super-implement/RUN.json`` (read-only helpers)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.verify.impl_verify.preflight import change_root


def super_implement_run_path(change_id: str, *, repo_root: Path) -> Path:
    """Путь к ``super-implement/RUN.json`` под change."""
    return change_root(change_id, repo_root=repo_root) / "super-implement" / "RUN.json"


def load_super_implement_run(
    change_id: str,
    *,
    repo_root: Path,
) -> dict[str, Any] | None:
    """Прочитать RUN.json если существует."""
    path = super_implement_run_path(change_id, repo_root=repo_root)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def is_super_implement_apply_complete(
    change_id: str,
    *,
    repo_root: Path,
) -> bool:
    """True если durable receipt ``super-implement/RUN.json`` в ``APPLY_COMPLETE``."""
    run = load_super_implement_run(change_id, repo_root=repo_root)
    if run is None:
        return False
    return str(run.get("status") or "").strip() == "APPLY_COMPLETE"


def read_live_execution_authorized(
    change_id: str,
    *,
    repo_root: Path,
    state_flag: bool | None = None,
) -> bool:
    """Fail-closed live gate: state override, иначе RUN.json, иначе False."""
    if state_flag is not None:
        return bool(state_flag)
    run = load_super_implement_run(change_id, repo_root=repo_root)
    if run is None:
        return False
    return bool(run.get("live_execution_authorized"))
