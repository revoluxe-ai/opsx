"""Durable S0 intake checkpoint (crash before runtime_state publication)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .mission_identity import bind_identity, findings_digest


def checkpoint_path(repo_root: Path, mission_id: str) -> Path:
    return repo_root / "opsx" / "runs" / "orch-missions" / mission_id / "s0-intake-checkpoint.json"


def persist_intake_checkpoint(
    repo_root: Path,
    *,
    mission_id: str,
    request: dict[str, Any],
    findings: list[dict[str, str]],
    work_items: list[dict[str, Any]],
) -> None:
    path = checkpoint_path(repo_root, mission_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    identity_state: dict[str, Any] = {}
    bind_identity(identity_state, request, findings)
    payload = {
        "schema_version": "opsx-s0-intake-checkpoint-v1",
        "mission_id": mission_id,
        "findings_digest": findings_digest(findings),
        "work_items": work_items,
        "identity": identity_state.get("identity"),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_intake_checkpoint(repo_root: Path, mission_id: str) -> dict[str, Any] | None:
    path = checkpoint_path(repo_root, mission_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def reconcile_work_items_from_checkpoint(
    repo_root: Path,
    *,
    mission_id: str,
    request: dict[str, Any],
    findings: list[dict[str, str]],
) -> list[dict[str, Any]] | None:
    checkpoint = load_intake_checkpoint(repo_root, mission_id)
    if checkpoint is None:
        return None
    digest = findings_digest(findings)
    if str(checkpoint.get("findings_digest") or "") != digest:
        return None
    identity = checkpoint.get("identity")
    if isinstance(identity, dict):
        mission = str(request.get("mission") or "")
        if identity.get("mission_id") != mission:
            return None
        if identity.get("execution_id") != str(request.get("execution_id") or ""):
            return None
        if identity.get("head") != str(request.get("head") or ""):
            return None
    items = checkpoint.get("work_items")
    if not isinstance(items, list) or not items:
        return None
    out: list[dict[str, Any]] = []
    for row in items:
        if isinstance(row, dict) and row.get("finding_id") and row.get("case_id"):
            out.append(dict(row))
    return out or None
