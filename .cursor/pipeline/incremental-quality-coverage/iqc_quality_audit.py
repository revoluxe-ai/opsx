"""Quality audit (read-only) для engineering adoption и gate."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from iqc_paths import CONTRACT_ID, EVIDENCE_CONTRACT, REPO_ROOT
from iqc_policy import IQCPolicyError, configured_py_files, load_manifest, manifest_fingerprint, verified_include_set
from iqc_required_change import resolve_required_change_inventory
from iqc_scope import build_scope_snapshot, paths_needing_expansion
from iqc_states import ManifestActivation, VerifiedScopeLayer
from iqc_verified_evidence import collect_verified_scope_evidence, evidence_binding_fingerprint


def audit_incremental_quality_coverage(root: Path = REPO_ROOT) -> dict[str, Any]:
    """Формирует INCREMENTAL_QUALITY_COVERAGE_REPORT (+ optional pyright evidence)."""
    try:
        manifest = load_manifest(root)
    except IQCPolicyError as err:
        return {
            "STATUS": "BLOCKED",
            "REASON": err.code,
            "DETAIL": str(err),
            "evidence_contract": EVIDENCE_CONTRACT,
            "contract_id": CONTRACT_ID,
        }

    inventory = resolve_required_change_inventory(root)
    if inventory["status"] == "GIT_UNAVAILABLE":
        return {
            "STATUS": "BLOCKED",
            "REASON": inventory["reason"],
            "evidence_contract": EVIDENCE_CONTRACT,
            "contract_id": CONTRACT_ID,
        }
    if inventory["status"] == "BASE_UNRESOLVED":
        return {
            "STATUS": "BLOCKED",
            "REASON": "GIT_BASE_UNRESOLVED",
            "evidence_contract": EVIDENCE_CONTRACT,
            "contract_id": CONTRACT_ID,
            "required_change_inventory": {
                "status": inventory["status"],
                "base_sha": inventory["base_sha"],
            },
        }

    snapshot = build_scope_snapshot(root)
    configured = configured_py_files(root, manifest)
    verified = verified_include_set(root, manifest)
    required = tuple(inventory["paths"])
    expansion = paths_needing_expansion(required, configured=configured, verified=verified)

    skip_pyright = os.environ.get("OPSX_IQC_SKIP_PYRIGHT_EVIDENCE", "").strip() == "1"
    run_pyright = not skip_pyright
    verified_evidence = collect_verified_scope_evidence(root, manifest, run_pyright=run_pyright)
    evidence_layer = str(verified_evidence.get("layer", VerifiedScopeLayer.CANDIDATE_VERIFIED.value))
    candidate_verified_count = int(verified_evidence.get("CANDIDATE_VERIFIED_SCOPE_count", 0))
    evidence_verified_count = int(verified_evidence.get("EVIDENCE_VERIFIED_SCOPE_count", 0))
    binding = verified_evidence.get("binding") or {}
    pyright_exit = binding.get("pyright_exit_code")
    pyright_meta = verified_evidence.get("pyright") or {}
    pyright_ran = bool(pyright_meta.get("ran"))
    pyright_unavailable = pyright_meta.get("reason") == "PYRIGHT_UNAVAILABLE"

    status = "PASS"
    fail_reason: str | None = None
    if expansion and manifest.activation == ManifestActivation.ACTIVE:
        status = "FAIL"
        fail_reason = "COVERAGE_EXPANSION_REQUIRED"
    elif expansion and manifest.activation == ManifestActivation.PROPOSED:
        status = "PASS_WITH_GAPS"
    elif manifest.activation == ManifestActivation.ACTIVE:
        if skip_pyright:
            status = "FAIL"
            fail_reason = "PYRIGHT_EVIDENCE_SKIPPED"
        elif pyright_unavailable:
            status = "BLOCKED"
            fail_reason = "PYRIGHT_UNAVAILABLE"
        elif not pyright_ran or pyright_exit != 0:
            status = "FAIL"
            fail_reason = "PYRIGHT_EVIDENCE_FAILED"
        elif evidence_layer != VerifiedScopeLayer.EVIDENCE_VERIFIED.value:
            status = "FAIL"
            fail_reason = "EVIDENCE_LAYER_NOT_VERIFIED"

    report: dict[str, Any] = {
        "STATUS": status,
        "evidence_contract": EVIDENCE_CONTRACT,
        "contract_id": CONTRACT_ID,
        "manifest_path": str(manifest.path.relative_to(root)),
        "manifest_activation": manifest.activation.value,
        "manifest_revision": manifest.revision,
        "policy_fingerprint": manifest_fingerprint(manifest, root=root),
        "evidence_binding_fingerprint": evidence_binding_fingerprint(verified_evidence),
        "verified_scope_layer": evidence_layer,
        "scopes": {
            "CONFIGURED_SCOPE_count": snapshot["configured_count"],
            "CANDIDATE_VERIFIED_SCOPE_count": candidate_verified_count,
            "EVIDENCE_VERIFIED_SCOPE_count": evidence_verified_count,
            "VERIFIED_SCOPE_count": candidate_verified_count,
            "REQUIRED_CHANGE_SCOPE_count": len(required),
            "LEGACY_UNCOVERED_count": snapshot["legacy_uncovered_count"],
        },
        "required_change_inventory": {
            "status": inventory["status"],
            "base_sha": inventory["base_sha"],
            "path_count": len(required),
        },
        "verified_scope_evidence": verified_evidence,
        "expansion_needed": list(expansion),
        "broad_include_ne_verified": snapshot["configured_count"] != candidate_verified_count,
        "no_auto_evidence_from_config_only": evidence_verified_count < candidate_verified_count
        or evidence_layer != VerifiedScopeLayer.EVIDENCE_VERIFIED.value,
        "execution_id": binding.get("execution_id"),
        "provenance_git_head": binding.get("git_head"),
        "verified_paths_content_digest": binding.get("verified_paths_content_digest"),
    }
    if fail_reason:
        report["REASON"] = fail_reason
    return report
