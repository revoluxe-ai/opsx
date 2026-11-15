"""Parent /opsx-bug-team dispatch records and idempotent dispatch identity."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

HOST_ENTRYPOINT = "/opsx-bug-team"
HOST_CLI = ".cursor/pipeline/bug-team/harness/opsx_bug_team_host_dispatch.py"
TRANSPORT = "IN_PARENT_SYNC"


def dispatch_identity(mission_id: str, execution_id: str, finding_id: str) -> str:
    raw = f"{mission_id}:{execution_id}:{finding_id}:bug-team-v1"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dispatch_path(repo_root: Path, mission_id: str, finding_id: str) -> Path:
    return (
        repo_root
        / "opsx"
        / "runs"
        / "orch-missions"
        / mission_id
        / "bug-team-dispatch"
        / f"{finding_id}.json"
    )


def record_dispatch(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    case_id: str,
    finding_id: str,
    plan_id: str,
    work_item_id: str,
    work_item_scope_paths: list[str] | None = None,
    preexisting_partial_repair_paths: list[str] | None = None,
    forbidden_paths: list[str] | None = None,
    member_ids: list[str] | None = None,
    disposition_id: str | None = None,
) -> dict[str, Any]:
    path = _dispatch_path(repo_root, mission_id, finding_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = dispatch_identity(mission_id, execution_id, finding_id)
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"STATUS": "FAIL_CLOSED", "REASON": "corrupt_dispatch_record"}
        if str(existing.get("dispatch_identity") or "") != identity:
            return {"STATUS": "FAIL_CLOSED", "REASON": "dispatch_identity_collision"}
        if str(existing.get("head") or "") != head:
            return {"STATUS": "FAIL_CLOSED", "REASON": "stale_dispatch_revision"}
        return {
            "STATUS": "DISPATCH_RECORDED",
            "IDEMPOTENT": "YES",
            "DISPATCH_ID": identity,
            "DISPATCH_PATH": str(path),
            "HOST_ARGS": existing.get("host_args"),
            "TRANSPORT": TRANSPORT,
            "NEXT_COMMAND": HOST_ENTRYPOINT,
            "TEMPORAL_NESTED_START": str(existing.get("temporal_nested_start") or "NO").upper(),
        }
    payload = {
        "schema_version": "opsx-bug-team-dispatch-v1",
        "dispatch_identity": identity,
        "mission_id": mission_id,
        "execution_id": execution_id,
        "head": head,
        "case_id": case_id,
        "finding_id": finding_id,
        "plan_id": plan_id,
        "work_item_id": work_item_id,
        "WORK_ITEM_SCOPE_PATHS": list(work_item_scope_paths or []),
        "PREEXISTING_PARTIAL_REPAIR_PATHS": list(preexisting_partial_repair_paths or []),
        "FORBIDDEN_PATHS": list(forbidden_paths or []),
        "member_ids": list(member_ids or []),
        "disposition_id": disposition_id or "",
        "host_entrypoint": HOST_ENTRYPOINT,
        "host_cli": HOST_CLI,
        "host_args": f"case={case_id}",
        "transport": TRANSPORT,
        "temporal_nested_start": "NO",
        "status": "DISPATCH_RECORDED",
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return {
        "STATUS": "DISPATCH_RECORDED",
        "IDEMPOTENT": "NO",
        "DISPATCH_ID": identity,
        "DISPATCH_PATH": str(path),
        "HOST_ARGS": payload["host_args"],
        "TRANSPORT": TRANSPORT,
        "NEXT_COMMAND": HOST_ENTRYPOINT,
        "HOST_CLI": HOST_CLI,
        "TEMPORAL_NESTED_START": "NO",
    }


def harvest_terminal_marker_path(repo_root: Path, mission_id: str, execution_id: str) -> Path:
    return (
        repo_root
        / "opsx"
        / "runs"
        / "orch-missions"
        / mission_id
        / f"execution-terminal-{execution_id}.json"
    )


def write_execution_terminal(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    source: str,
) -> None:
    path = harvest_terminal_marker_path(repo_root, mission_id, execution_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mission_id": mission_id,
        "execution_id": execution_id,
        "head": head,
        "execution_terminal": "CONFIRMED",
        "source": source,
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
