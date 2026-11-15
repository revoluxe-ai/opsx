"""Owner-visible monitoring manifests for `/tempo-*` on Cursor Cloud Agent VMs.

Temporal Web UI listens on loopback inside the agent VM only. This module writes
durable JSON the Owner (and coordinating agents) can read without SSH or public UI
exposure: repo path under `opsx/runs/temporal/monitoring/` and a mirror under
`/opt/cursor/artifacts/` when that directory exists.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run_store import durable_runs_dir

SCHEMA_VERSION = "opsx-temporal-owner-monitoring-v1"
ARTIFACT_BASENAME = "temporal-pipeline-monitoring.json"


def durable_monitoring_dir(repo_root: str | Path) -> Path:
    """Durable owner monitoring manifests: `opsx/runs/temporal/monitoring`."""
    root = Path(repo_root).resolve()
    return resource_path(root, "opsx", "runs", "temporal", "monitoring")


def latest_manifest_path(repo_root: str | Path) -> Path:
    """Pointer file for the active or most recent pipeline run on this VM."""
    return durable_monitoring_dir(repo_root) / "latest.json"


def workflow_manifest_path(repo_root: str | Path, workflow_id: str) -> Path:
    """Per-workflow manifest (stable name once workflow_id is known)."""
    safe = workflow_id.replace("/", "_")
    return durable_monitoring_dir(repo_root) / f"{safe}.json"


def artifacts_monitoring_path() -> Path:
    """Cloud Agent artifact upload path (created when parent dir exists)."""
    return Path("/opt/cursor/artifacts") / ARTIFACT_BASENAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _execution_context() -> str:
    if os.environ.get("CURSOR_CLOUD") == "1" or os.environ.get("CURSOR_AGENT"):
        return "cursor_cloud_agent"
    if Path("/opt/cursor/artifacts").is_dir():
        return "cursor_cloud_agent"
    return "local"


def _cursor_agent_run_url() -> str | None:
    for key in ("CURSOR_CLOUD_BC_ID", "CURSOR_AGENT_BC_ID", "CURSOR_BC_ID"):
        bc_id = (os.environ.get(key) or "").strip()
        if bc_id:
            return f"https://cursor.com/agents/{bc_id}"
    return None


def build_status_poll_command(repo: Path, workflow_id: str | None) -> str | None:
    """Shell command an agent can run to refresh workflow status JSON."""
    if not workflow_id:
        return None
    repo_s = str(repo.resolve())
    return (
        f"python tools/temporal/runtime.py --repo {repo_s} "
        f"status --workflow-id {workflow_id}"
    )


def build_monitoring_manifest(
    *,
    repo: Path,
    pipeline: str,
    phase: str,
    resolved: dict[str, str],
    review: str,
    repair: str,
    workflow_id: str | None = None,
    operator_status: str | None = None,
    outcome: str | None = None,
    result_file: str | None = None,
    temporal_ui: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a owner-facing monitoring document (no secrets, no public UI bind)."""
    runs_dir = durable_runs_dir(repo)
    wf_result = (
        str(runs_dir / f"{workflow_id}.json") if workflow_id else None
    )
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": _utc_now(),
        "execution_context": _execution_context(),
        "phase": phase,
        "pipeline": pipeline,
        "review_tier": review,
        "repair_tier": repair,
        "base": resolved.get("base"),
        "head": resolved.get("head"),
        "change": resolved.get("change"),
        "workflow_id": workflow_id,
        "operator_status": operator_status,
        "outcome": outcome,
        "result_file": result_file or wf_result,
        "temporal_ui_vm_local": temporal_ui,
        "temporal_ui_owner_note": (
            "VM loopback only — opening this URL on Owner Windows does not show this workflow."
        ),
        "cursor_agent_run_url": _cursor_agent_run_url(),
        "status_poll_command": build_status_poll_command(repo, workflow_id),
        "paths": {
            "repo_latest_manifest": str(latest_manifest_path(repo)),
            "repo_workflow_manifest": (
                str(workflow_manifest_path(repo, workflow_id)) if workflow_id else None
            ),
            "workflow_status_json": wf_result,
            "scratch_temporal": str(resource_path(repo, ".tmp", "temporal")),
        },
        "owner_playbook_doc": "docs/opsx/cloud-temporal-monitoring.md",
    }
    if extra:
        manifest["extra"] = extra
    return manifest


def write_owner_monitoring_manifest(
    repo: Path,
    payload: dict[str, Any],
    *,
    workflow_id: str | None = None,
) -> Path:
    """Persist manifest to repo monitoring dir and mirror to artifacts when possible."""
    dest_dir = durable_monitoring_dir(repo)
    dest_dir.mkdir(parents=True, exist_ok=True)
    latest = latest_manifest_path(repo)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    latest.write_text(text, encoding="utf-8")
    if workflow_id:
        per_wf = workflow_manifest_path(repo, workflow_id)
        per_wf.write_text(text, encoding="utf-8")
    mirror_monitoring_to_artifacts(payload)
    return latest


def mirror_monitoring_to_artifacts(payload: dict[str, Any]) -> Path | None:
    """Copy latest manifest into Cursor Cloud artifacts (uploaded to Owner)."""
    art_dir = Path("/opt/cursor/artifacts")
    if not art_dir.is_dir():
        return None
    dest = artifacts_monitoring_path()
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return dest


def read_latest_manifest(repo: Path) -> dict[str, Any] | None:
    """Load latest monitoring manifest if present."""
    path = latest_manifest_path(repo)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
