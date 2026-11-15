"""Связь revision IQC manifest с bounded-verify supersession."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iqc_policy import IQCPolicyError, load_manifest, manifest_fingerprint
from iqc_paths import REPO_ROOT

IQC_POLICY_REVISION = "IQC_POLICY_REVISION"


def policy_revision_block_reason(
    frozen_fingerprint: str,
    *,
    root: Path = REPO_ROOT,
) -> str | None:
    """Возвращает IQC_POLICY_REVISION если live manifest fingerprint изменился."""
    try:
        manifest = load_manifest(root)
    except IQCPolicyError:
        return "IQC_MANIFEST_MISSING"
    live = manifest_fingerprint(manifest, root=root)
    if frozen_fingerprint and frozen_fingerprint != live:
        return IQC_POLICY_REVISION
    return None


def live_policy_binding(*, root: Path = REPO_ROOT) -> dict[str, Any]:
    """Binding поля для temporal / convergence guards."""
    try:
        manifest = load_manifest(root)
    except IQCPolicyError as err:
        return {"ok": False, "code": err.code}
    return {
        "ok": True,
        "policy_id": manifest.policy_id,
        "revision": manifest.revision,
        "activation": manifest.activation.value,
        "fingerprint": manifest_fingerprint(manifest, root=root),
    }
