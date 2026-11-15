#!/usr/bin/env python3
"""S4A custodian receipt verification (production + isolated test_custodian mode)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from trusted_policy_v1 import (
    ALLOWED_WORKFLOW_REF_PREFIXES,
    CANONICAL_REPOSITORY,
    FORBIDDEN_REVIEWER_ACTOR_PREFIXES,
    TRUST_POLICY_IMPLEMENTATION_ID,
    acquisition_workflows_for_mode,
    review_workflows_for_mode,
)

TrustPolicy = Literal["production", "test_fixture", "test_custodian"]

CUSTODIAN_ACQUISITION_DIR = Path("opsx/trusted-provenance/acquisitions")
CUSTODIAN_REVIEW_DIR = Path("opsx/trusted-provenance/reviews")

CUSTODIAN_ISSUER_REJECTED = "CUSTODIAN_ISSUER_REJECTED"
CUSTODIAN_RECEIPT_INVALID = "CUSTODIAN_RECEIPT_INVALID"
TRUSTED_PROVENANCE_UNAVAILABLE = "TRUSTED_PROVENANCE_UNAVAILABLE_S3"
CLAIM_COMPATIBILITY_UNVERIFIED = "CLAIM_COMPATIBILITY_UNVERIFIED"
FALSE_NOT_APPLICABLE_REJECTED = "FALSE_NOT_APPLICABLE_REJECTED"


class CustodianFinding:
    def __init__(self, code: str, detail: str, claim_id: str | None = None) -> None:
        self.code = code
        self.detail = detail
        self.claim_id = claim_id


def _normalize_repo(repo: str) -> str:
    return repo.lower().removeprefix("https://").removeprefix("http://").rstrip("/")


def _validate_issuer(
    issuer: dict[str, Any],
    *,
    allowed_workflows: frozenset[str],
    design_writer_id: str,
    forbid_same_actor_as_writer: bool,
) -> list[CustodianFinding]:
    findings: list[CustodianFinding] = []
    if issuer.get("kind") != "github_actions_workflow":
        findings.append(CustodianFinding(CUSTODIAN_ISSUER_REJECTED, "issuer.kind must be github_actions_workflow"))
        return findings
    repo = _normalize_repo(str(issuer.get("repository") or ""))
    if repo != _normalize_repo(CANONICAL_REPOSITORY):
        findings.append(CustodianFinding(CUSTODIAN_ISSUER_REJECTED, "repository not canonical"))
    workflow_path = str(issuer.get("workflow_path") or "")
    if workflow_path not in allowed_workflows:
        findings.append(CustodianFinding(CUSTODIAN_ISSUER_REJECTED, "workflow_path not in pinned trust policy"))
    workflow_ref = str(issuer.get("workflow_ref") or "")
    if not any(workflow_ref.startswith(prefix) for prefix in ALLOWED_WORKFLOW_REF_PREFIXES):
        findings.append(CustodianFinding(CUSTODIAN_ISSUER_REJECTED, "workflow_ref not allowed"))
    if not issuer.get("run_id"):
        findings.append(CustodianFinding(CUSTODIAN_RECEIPT_INVALID, "missing run_id"))
    actor = str(issuer.get("actor") or "")
    if forbid_same_actor_as_writer and actor and actor == design_writer_id:
        findings.append(CustodianFinding(CUSTODIAN_ISSUER_REJECTED, "issuer actor equals design_writer_id"))
    if not re.match(r"^[0-9]+$", str(issuer.get("run_id") or "")):
        findings.append(CustodianFinding(CUSTODIAN_RECEIPT_INVALID, "run_id must be numeric workflow run"))
    return findings


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def acquisition_custodian_path(repo_root: Path, change_id: str, capture_id: str) -> Path:
    return repo_root / CUSTODIAN_ACQUISITION_DIR / change_id / f"{capture_id}.json"


def review_custodian_path(repo_root: Path, change_id: str) -> Path:
    return repo_root / CUSTODIAN_REVIEW_DIR / change_id / "compatibility.json"


def validate_acquisition_custodian(
    payload: dict[str, Any],
    *,
    trust_policy: TrustPolicy,
    design_writer_id: str,
    expected_change_id: str,
    expected_inputs_digest: str,
    design_digest: str,
    manifest: dict[str, Any],
) -> list[CustodianFinding]:
    findings: list[CustodianFinding] = []
    if payload.get("kind") != "CUSTODIAN_ACQUISITION":
        findings.append(CustodianFinding(CUSTODIAN_RECEIPT_INVALID, "kind mismatch"))
        return findings
    exec_id = payload.get("execution_identity") or {}
    if exec_id.get("trust_policy_implementation_id") != TRUST_POLICY_IMPLEMENTATION_ID:
        findings.append(CustodianFinding(CUSTODIAN_RECEIPT_INVALID, "trust_policy_implementation_id mismatch"))
    if exec_id.get("cursor_bc_id"):
        findings.append(
            CustodianFinding(
                CUSTODIAN_RECEIPT_INVALID,
                "cursor_bc_id is execution metadata only, not trust proof",
            )
        )
    findings.extend(
        _validate_issuer(
            payload.get("issuer") or {},
            allowed_workflows=acquisition_workflows_for_mode(trust_policy),
            design_writer_id=design_writer_id,
            forbid_same_actor_as_writer=True,
        )
    )
    for field, expected in (
        ("change_id", expected_change_id),
        ("inputs_digest", expected_inputs_digest),
        ("design_digest", design_digest),
        ("capture_id", manifest.get("capture_id")),
        ("content_sha256", manifest.get("content_sha256")),
        ("source_uri", manifest.get("source_uri")),
        ("publisher", manifest.get("publisher")),
        ("documented_version", manifest.get("documented_version")),
    ):
        if payload.get(field) != expected:
            findings.append(CustodianFinding(CUSTODIAN_RECEIPT_INVALID, f"binding mismatch {field}"))
    claim_ids = payload.get("claim_ids") or []
    for cid in manifest.get("claim_ids") or []:
        if cid not in claim_ids:
            findings.append(CustodianFinding(CUSTODIAN_RECEIPT_INVALID, f"claim_id {cid} not in custodian"))
    if payload.get("acquisition_findings") and any(
        "COMPATIBILITY_PASS" in str(item) for item in payload.get("acquisition_findings") or []
    ):
        findings.append(
            CustodianFinding(
                CUSTODIAN_RECEIPT_INVALID,
                "acquisition custodian cannot assert compatibility PASS",
            )
        )
    return findings


def validate_review_custodian(
    payload: dict[str, Any],
    *,
    trust_policy: TrustPolicy,
    design_writer_id: str,
    acquisition_actor: str | None,
    expected_change_id: str,
    expected_inputs_digest: str,
    design_digest: str,
    bound_receipt_sha256: str,
    receipt: dict[str, Any],
) -> list[CustodianFinding]:
    findings: list[CustodianFinding] = []
    if payload.get("kind") != "CUSTODIAN_COMPATIBILITY_REVIEW":
        findings.append(CustodianFinding(CUSTODIAN_RECEIPT_INVALID, "review kind mismatch"))
        return findings
    exec_id = payload.get("execution_identity") or {}
    if exec_id.get("trust_policy_implementation_id") != TRUST_POLICY_IMPLEMENTATION_ID:
        findings.append(CustodianFinding(CUSTODIAN_RECEIPT_INVALID, "review trust_policy id mismatch"))
    issuer = payload.get("issuer") or {}
    findings.extend(
        _validate_issuer(
            issuer,
            allowed_workflows=review_workflows_for_mode(trust_policy),
            design_writer_id=design_writer_id,
            forbid_same_actor_as_writer=True,
        )
    )
    actor = str(issuer.get("actor") or "")
    if acquisition_actor and actor == acquisition_actor:
        findings.append(
            CustodianFinding(CUSTODIAN_ISSUER_REJECTED, "review issuer must differ from acquisition actor")
        )
    for prefix in FORBIDDEN_REVIEWER_ACTOR_PREFIXES:
        if actor.startswith(prefix) and trust_policy == "production":
            findings.append(
                CustodianFinding(
                    CUSTODIAN_ISSUER_REJECTED,
                    "CI bot cannot be independent compatibility reviewer in production",
                )
            )
    for field, expected in (
        ("change_id", expected_change_id),
        ("inputs_digest", expected_inputs_digest),
        ("design_digest", design_digest),
        ("bound_receipt_sha256", bound_receipt_sha256),
    ):
        if payload.get(field) != expected:
            findings.append(CustodianFinding(CUSTODIAN_RECEIPT_INVALID, f"review binding mismatch {field}"))
    reviews_by_id = {
        str(item.get("claim_id")): item
        for item in payload.get("claim_reviews") or []
        if isinstance(item, dict)
    }
    for claim in receipt.get("claims") or []:
        if not isinstance(claim, dict) or claim.get("result") != "PASS":
            continue
        cid = str(claim.get("claim_id"))
        review = reviews_by_id.get(cid)
        if review is None:
            findings.append(
                CustodianFinding(CLAIM_COMPATIBILITY_UNVERIFIED, f"missing claim review {cid}", cid)
            )
            continue
        if review.get("compatibility") != "PASS":
            findings.append(
                CustodianFinding(CLAIM_COMPATIBILITY_UNVERIFIED, f"review not PASS for {cid}", cid)
            )
            continue
        facts = review.get("verified_facts") or {}
        library = claim.get("library") or {}
        if library.get("name") and facts.get("library_name") != library.get("name"):
            findings.append(CustodianFinding(CLAIM_COMPATIBILITY_UNVERIFIED, "library_name", cid))
        if library.get("version_claim") and facts.get("version_claim") != library.get("version_claim"):
            findings.append(CustodianFinding(CLAIM_COMPATIBILITY_UNVERIFIED, "version_claim", cid))
    return findings


def validate_production_custodian_trust(
    receipt: dict[str, Any],
    *,
    repo_root: Path,
    change_root: Path,
    expected_change_id: str,
    expected_inputs_digest: str,
    design_digest: str,
    trust_policy: TrustPolicy,
) -> list[CustodianFinding]:
    """S4A: custodian ledger required for production material trust."""

    if trust_policy == "test_fixture":
        return []

    findings: list[CustodianFinding] = []
    writer = str(receipt.get("design_writer_id") or "")
    receipt_path = change_root / "architecture-evidence" / "ARCH-EVIDENCE.json"
    bound_receipt = ""
    if receipt_path.is_file():
        import hashlib

        bound_receipt = hashlib.sha256(receipt_path.read_bytes()).hexdigest()

    from doc_capture import load_capture_manifest  # noqa: WPS433

    acquisition_actor: str | None = None
    for claim in receipt.get("claims") or []:
        if not isinstance(claim, dict) or claim.get("result") != "PASS":
            continue
        cid = str(claim.get("claim_id"))
        uses_capture = any(
            isinstance(s, dict) and s.get("kind") == "captured_doc" for s in claim.get("sources") or []
        )
        if not uses_capture:
            continue
        for source in claim.get("sources") or []:
            if not isinstance(source, dict) or source.get("kind") != "captured_doc":
                continue
            capture_id = str(source.get("capture_id") or "")
            manifest = load_capture_manifest(change_root, capture_id)
            if manifest is None:
                findings.append(CustodianFinding(TRUSTED_PROVENANCE_UNAVAILABLE, "missing capture", cid))
                continue
            if trust_policy == "production":
                findings.append(
                    CustodianFinding(
                        TRUSTED_PROVENANCE_UNAVAILABLE,
                        "production custodian issuance not available (S4B)",
                        cid,
                    )
                )
                continue
            cust_path = acquisition_custodian_path(repo_root, expected_change_id, capture_id)
            payload = _load_json(cust_path)
            if payload is None:
                findings.append(
                    CustodianFinding(TRUSTED_PROVENANCE_UNAVAILABLE, f"missing custodian acquisition {capture_id}", cid)
                )
                continue
            findings.extend(
                validate_acquisition_custodian(
                    payload,
                    trust_policy=trust_policy,
                    design_writer_id=writer,
                    expected_change_id=expected_change_id,
                    expected_inputs_digest=expected_inputs_digest,
                    design_digest=design_digest,
                    manifest=manifest,
                )
            )
            acquisition_actor = str((payload.get("issuer") or {}).get("actor") or "") or acquisition_actor

    critical_pass = any(
        isinstance(c, dict) and c.get("result") == "PASS" for c in receipt.get("claims") or []
    )
    if receipt.get("applicability") == "REQUIRED" and critical_pass:
        if trust_policy == "production":
            findings.append(
                CustodianFinding(
                    TRUSTED_PROVENANCE_UNAVAILABLE,
                    "production compatibility review custodian not available (S4B)",
                )
            )
        else:
            review_path = review_custodian_path(repo_root, expected_change_id)
            review_payload = _load_json(review_path)
            if review_payload is None:
                findings.append(
                    CustodianFinding(TRUSTED_PROVENANCE_UNAVAILABLE, "missing custodian compatibility review")
                )
            else:
                findings.extend(
                    validate_review_custodian(
                        review_payload,
                        trust_policy=trust_policy,
                        design_writer_id=writer,
                        acquisition_actor=acquisition_actor,
                        expected_change_id=expected_change_id,
                        expected_inputs_digest=expected_inputs_digest,
                        design_digest=design_digest,
                        bound_receipt_sha256=bound_receipt,
                        receipt=receipt,
                    )
                )

    return findings
