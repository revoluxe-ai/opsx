"""Operator-supplied admission binding for PBH (independent of workflow self-defaults).

Authority is stored per ``(workflow_id, workflow_run_id)`` — not a single mutable
file keyed only by ``workflow_id``. ``start.py`` publishes the manifest atomically
**after** a successful ``start_workflow`` (when ``run_id`` is known); failed duplicate
starts never write admission evidence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "pbh-admission-binding-v1"


class AdmissionManifestConflictError(ValueError):
    """A sealed admission manifest already exists with different binding fields."""


def _safe_segment(value: str) -> str:
    return value.replace("/", "-").replace("\\", "-")


def admission_manifest_path(
    repo: str | Path,
    workflow_id: str,
    workflow_run_id: str,
) -> Path:
    """Durable path for one execution's admission evidence."""
    from run_store import durable_gate_dir

    wf = _safe_segment(workflow_id)
    run = _safe_segment(workflow_run_id.strip())
    return durable_gate_dir(repo) / "pbh-admission" / wf / f"{run}.json"


def _build_manifest_payload(
    *,
    workflow_id: str,
    workflow_run_id: str,
    expected_candidate_sha: str,
    control_plane_code_identity: str,
) -> dict[str, Any]:
    run_id = workflow_run_id.strip()
    candidate = expected_candidate_sha.strip()
    identity = control_plane_code_identity.strip()
    if not workflow_id.strip():
        raise ValueError("workflow_id is required for admission manifest")
    if not run_id:
        raise ValueError("workflow_run_id is required for admission manifest")
    if not candidate:
        raise ValueError("expected_candidate_sha is required for admission manifest")
    if not identity:
        raise ValueError("control_plane_code_identity is required for admission manifest")
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "workflow_run_id": run_id,
        "expected_candidate_sha": candidate,
        "control_plane_code_identity": identity,
        "binding_complete": True,
    }


def is_admission_manifest_complete(manifest: dict[str, Any] | None) -> bool:
    """True only when all binding fields are present (no partial authority)."""
    if manifest is None:
        return False
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return False
    if manifest.get("binding_complete") is not True:
        return False
    for key in (
        "workflow_id",
        "workflow_run_id",
        "expected_candidate_sha",
        "control_plane_code_identity",
    ):
        if not str(manifest.get(key) or "").strip():
            return False
    return True


def publish_admission_manifest_atomic(
    repo: str | Path,
    *,
    workflow_id: str,
    workflow_run_id: str,
    expected_candidate_sha: str,
    control_plane_code_identity: str,
) -> Path:
    """Atomically publish sealed admission evidence for one Temporal execution.

    Idempotent when the on-disk payload matches. Conflicting republication for the
    same ``workflow_run_id`` raises ``AdmissionManifestConflictError``.
    """
    path = admission_manifest_path(repo, workflow_id, workflow_run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _build_manifest_payload(
        workflow_id=workflow_id,
        workflow_run_id=workflow_run_id,
        expected_candidate_sha=expected_candidate_sha,
        control_plane_code_identity=control_plane_code_identity,
    )
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise AdmissionManifestConflictError(
                f"corrupt admission manifest at {path}; manual cleanup required"
            )
        if not isinstance(existing, dict):
            raise AdmissionManifestConflictError(f"invalid admission manifest at {path}")
        if existing == payload:
            return path
        raise AdmissionManifestConflictError(
            "admission manifest already sealed with different binding for this run_id"
        )
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_admission_manifest(
    repo: str | Path,
    workflow_id: str,
    workflow_run_id: str,
) -> dict[str, Any] | None:
    """Load complete manifest for one execution, or ``None`` if missing/partial."""
    path = admission_manifest_path(repo, workflow_id, workflow_run_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if not is_admission_manifest_complete(data):
        return None
    if str(data.get("workflow_id") or "") != workflow_id:
        return None
    if str(data.get("workflow_run_id") or "").strip() != workflow_run_id.strip():
        return None
    return data


def validate_admission_binding(
    manifest: dict[str, Any] | None,
    *,
    workflow_id: str,
    workflow_run_id: str,
    observed_candidate_sha: str,
    expected_control_plane_code_identity: str,
    relax_run_id_seal: bool = False,
) -> tuple[str | None, str]:
    """Return ``(error_status, message)`` or ``(None, '')`` when binding is valid."""
    if manifest is None:
        return (
            "WORKER_CODE_IDENTITY_UNVERIFIED",
            f"missing admission manifest for workflow_id={workflow_id} "
            f"workflow_run_id={workflow_run_id.strip()}",
        )
    if not is_admission_manifest_complete(manifest):
        return (
            "WORKER_CODE_IDENTITY_UNVERIFIED",
            "admission manifest incomplete or partial; operator must republish atomically",
        )
    if manifest.get("schema_version") != SCHEMA_VERSION:
        return ("WORKER_CODE_IDENTITY_UNVERIFIED", "admission manifest schema mismatch")
    if str(manifest.get("workflow_id") or "") != workflow_id:
        return ("WORKER_CODE_IDENTITY_UNVERIFIED", "admission manifest workflow_id mismatch")
    sealed_run = str(manifest.get("workflow_run_id") or "").strip()
    if not sealed_run:
        return (
            "WORKER_CODE_IDENTITY_UNVERIFIED",
            "admission manifest workflow_run_id not sealed yet",
        )
    if relax_run_id_seal:
        sealed_run = workflow_run_id.strip()
    if sealed_run != workflow_run_id.strip():
        return (
            "WORKER_CODE_IDENTITY_UNVERIFIED",
            "admission manifest workflow_run_id does not match execution",
        )
    manifest_candidate = str(manifest.get("expected_candidate_sha") or "").strip()
    observed = observed_candidate_sha.strip()
    if not manifest_candidate or not observed:
        return ("WORKER_CODE_IDENTITY_UNVERIFIED", "missing candidate_sha for admission binding")
    if manifest_candidate != observed:
        return (
            "WORKER_CODE_IDENTITY_UNVERIFIED",
            "candidate_sha does not match operator admission manifest",
        )
    manifest_identity = str(manifest.get("control_plane_code_identity") or "").strip()
    expected_identity = expected_control_plane_code_identity.strip()
    if not manifest_identity or not expected_identity:
        return ("WORKER_CODE_IDENTITY_UNVERIFIED", "missing control_plane_code_identity in admission")
    if manifest_identity != expected_identity:
        return (
            "WORKER_CODE_IDENTITY_UNVERIFIED",
            "control_plane_code_identity does not match admission manifest",
        )
    return None, ""
