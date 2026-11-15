# -*- coding: utf-8 -*-
"""Break-glass process deferral consult for bounded-verify frontier (no fake PASS)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]


def _node_json(root: Path, args: list[str]) -> dict[str, Any] | None:
    script = root / "opsx_tools" / "opsx-break-glass-process-gate-deferral.mjs"
    if not script.is_file():
        return None
    proc = subprocess.run(
        ["node", str(script), *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def is_mypy_fail_frontier_deferred(change_id: str, *, root: Path = REPO_ROOT) -> bool:
    """True when open owner override defers repo-wide mypy without minting PASS."""
    payload = _node_json(
        root,
        ["quality-deferral", "--change-id", change_id],
    )
    return bool(payload and payload.get("ok") and payload.get("deferred"))


def ledger_fail_blocks_frontier(change_id: str, check_id: str, *, root: Path = REPO_ROOT) -> bool:
    """FAIL entries that remain frontier-blocking (process-deferred mypy is non-blocking)."""
    if check_id == "mypy" and is_mypy_fail_frontier_deferred(change_id, root=root):
        return False
    return True
