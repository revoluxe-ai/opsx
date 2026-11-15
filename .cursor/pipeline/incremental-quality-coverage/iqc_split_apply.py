"""Split-APPLY preflight: REQUIRED_CHANGE ⊆ VERIFIED при ACTIVE."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from iqc_quality_audit import audit_incremental_quality_coverage
from iqc_paths import REPO_ROOT
from iqc_policy import IQCPolicyError, load_manifest
from iqc_states import ManifestActivation


def preflight_for_change(change_id: str, *, root: Path = REPO_ROOT) -> dict[str, Any]:
    """Advisory/ enforceable IQC summary для split-APPLY change."""
    report = audit_incremental_quality_coverage(root)
    try:
        activation = load_manifest(root).activation
    except IQCPolicyError:
        activation = None
    if activation == ManifestActivation.ACTIVE:
        ok = report.get("STATUS") == "PASS"
    else:
        ok = report.get("STATUS") in ("PASS", "PASS_WITH_GAPS")
    return {
        "change_id": change_id,
        "incremental_quality_coverage": report,
        "split_apply_iqc_ok": ok,
    }
