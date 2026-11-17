#!/usr/bin/env python3
"""S3 trusted provenance: tool-execution receipts and independent review attestations.

Агент записывает hash-bound receipts; gate проверяет offline без live MCP.
Self-reported capture/review metadata не является доверенным без receipt + fixture proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

TrustPolicy = Literal["production", "test_fixture", "test_custodian"]

CAPTURE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
TOOL_EXEC_DIR = Path("architecture-evidence/trusted-provenance/tool-executions")
REVIEW_ATTESTATION_PATH = Path("architecture-evidence/independent-review/REVIEW-ATTESTATION.json")
RECEIPT_FILENAME = "ARCH-EVIDENCE.json"

ALLOWED_ISSUER_PROFILES = frozenset(
    {
        "context7-mcp-offline-fixture-v1",
        "official-provider-export-offline-fixture-v1",
        "agent-readonly-fetch-offline-fixture-v1",
    }
)
ALLOWED_REVIEW_AUTHORITY_PROFILES = frozenset(
    {
        "architecture-evidence-independent-review-v1",
    }
)
ALLOWED_REVIEWER_AGENT_IDS = frozenset(
    {
        "independent-review-agent-fixture-v1",
    }
)
TEST_TRUST_FIXTURE_PREFIX = ".cursor/pipeline/architecture-evidence-tests/fixtures/"
ALLOWED_TOOL_PROOF_PREFIX = (
    ".cursor/pipeline/architecture-evidence-tests/fixtures/trusted-executions/"
)
ALLOWED_REVIEW_PROOF_PREFIX = (
    ".cursor/pipeline/architecture-evidence-tests/fixtures/trusted-review/"
)
PRODUCTION_TOOL_EXEC_PROOF_PREFIX = "opsx/trusted-provenance/tool-execution-proofs/"
PRODUCTION_REVIEW_EXEC_PROOF_PREFIX = "opsx/trusted-provenance/review-execution-proofs/"
TRUSTED_PROVENANCE_UNAVAILABLE = "TRUSTED_PROVENANCE_UNAVAILABLE_S3"


@dataclass
class TrustFinding:
    code: str
    detail: str
    claim_id: str | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _normalize_rel_path(rel: str) -> str:
    return rel.replace("\\", "/")


def _is_test_fixture_path(rel: str) -> bool:
    return _normalize_rel_path(rel).startswith(TEST_TRUST_FIXTURE_PREFIX)


def _production_tool_proof_allowed(rel: str) -> bool:
    return _normalize_rel_path(rel).startswith(PRODUCTION_TOOL_EXEC_PROOF_PREFIX)


def _production_review_proof_allowed(rel: str) -> bool:
    return _normalize_rel_path(rel).startswith(PRODUCTION_REVIEW_EXEC_PROOF_PREFIX)


def _tool_exec_path(change_root: Path, capture_id: str) -> Path:
    if not CAPTURE_ID_PATTERN.match(capture_id):
        raise ValueError("invalid capture_id")
    base = (change_root / TOOL_EXEC_DIR).resolve()
    target = (base / f"{capture_id}.tool-exec.json").resolve()
    if base not in target.parents:
        raise ValueError("path traversal")
    return target


def _load_execution_proof_fixture(repo_root: Path, rel_path: str) -> dict[str, Any]:
    path = (repo_root / rel_path).resolve()
    if not path.is_file():
        raise ValueError(f"missing execution proof fixture {rel_path}")
    return json.loads(path.read_text(encoding="utf-8"))


def record_tool_execution_receipt(
    *,
    change_root: Path,
    repo_root: Path,
    capture_id: str,
    change_id: str,
    inputs_digest: str,
    permitted_tool: str,
    issuer_profile: str,
    bound_content_sha256: str,
    source_uri: str,
    publisher: str,
    documented_version: str,
    execution_proof_fixture_path: str,
) -> dict[str, Any]:
    """Записать tool-execution receipt, привязанный к offline proof fixture."""

    if issuer_profile not in ALLOWED_ISSUER_PROFILES:
        raise ValueError("issuer_profile not permitted")
    rel_fixture = execution_proof_fixture_path.replace("\\", "/")
    if not rel_fixture.startswith(ALLOWED_TOOL_PROOF_PREFIX):
        raise ValueError("execution proof fixture outside allowlist")
    fixture_path = (repo_root / rel_fixture).resolve()
    if not fixture_path.is_file():
        raise ValueError("execution proof fixture missing")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if fixture.get("issuer_profile") != issuer_profile:
        raise ValueError("fixture issuer_profile mismatch")
    if fixture.get("permitted_tool") != permitted_tool:
        raise ValueError("fixture permitted_tool mismatch")
    if fixture.get("bound_content_sha256") != bound_content_sha256:
        raise ValueError("fixture bound_content_sha256 mismatch")
    for key in ("source_uri", "publisher", "documented_version"):
        if fixture.get(key) != locals()[key]:
            raise ValueError(f"fixture {key} mismatch")

    receipt_path = _tool_exec_path(change_root, capture_id)
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "trust_schema_version": 1,
        "receipt_kind": "TOOL_EXECUTION",
        "capture_id": capture_id,
        "change_id": change_id,
        "inputs_digest": inputs_digest,
        "permitted_tool": permitted_tool,
        "issuer_profile": issuer_profile,
        "bound_content_sha256": bound_content_sha256,
        "source_uri": source_uri,
        "publisher": publisher,
        "documented_version": documented_version,
        "execution_proof_fixture": {
            "path": execution_proof_fixture_path,
            "content_sha256": sha256_file(fixture_path),
        },
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    if receipt_path.is_file():
        existing = json.loads(receipt_path.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError("tool execution receipt conflict")
        return existing
    tmp = receipt_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(receipt_path)
    return payload


def validate_tool_execution_for_capture(
    *,
    repo_root: Path,
    change_root: Path,
    manifest: dict[str, Any],
    expected_change_id: str,
    expected_inputs_digest: str,
    receipt_inputs_digest: str,
    trust_policy: TrustPolicy = "production",
) -> list[TrustFinding]:
    """Проверить tool-execution receipt для captured_doc (S3 strict)."""

    findings: list[TrustFinding] = []
    capture_id = str(manifest.get("capture_id") or "")
    claim_ids = manifest.get("claim_ids") or []
    primary_claim = str(claim_ids[0]) if claim_ids else None

    if trust_policy in ("production", "test_custodian"):
        return findings

    try:
        receipt_path = _tool_exec_path(change_root, capture_id)
    except ValueError:
        findings.append(
            TrustFinding("DOC_CAPTURE_PATH_UNSAFE", "invalid capture_id", primary_claim)
        )
        return findings

    if not receipt_path.is_file():
        findings.append(
            TrustFinding(
                "SOURCE_PROVENANCE_UNTRUSTED_S3",
                f"missing tool execution receipt for {capture_id}",
                primary_claim,
            )
        )
        return findings

    tool_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if tool_receipt.get("receipt_kind") != "TOOL_EXECUTION":
        findings.append(
            TrustFinding("TOOL_EXECUTION_RECEIPT_INVALID", "receipt_kind", primary_claim)
        )
        return findings

    if tool_receipt.get("issuer_profile") not in ALLOWED_ISSUER_PROFILES:
        findings.append(
            TrustFinding(
                "TOOL_EXECUTION_RECEIPT_INVALID",
                "issuer_profile not permitted",
                primary_claim,
            )
        )

    for field, expected in (
        ("change_id", expected_change_id),
        ("inputs_digest", expected_inputs_digest),
        ("bound_content_sha256", manifest.get("content_sha256")),
        ("permitted_tool", manifest.get("acquisition_tool")),
        ("source_uri", manifest.get("source_uri")),
        ("publisher", manifest.get("publisher")),
        ("documented_version", manifest.get("documented_version")),
    ):
        if tool_receipt.get(field) != expected:
            findings.append(
                TrustFinding(
                    "TOOL_EXECUTION_RECEIPT_INVALID",
                    f"forged or mismatched tool receipt field {field}",
                    primary_claim,
                )
            )

    if manifest.get("inputs_digest") != receipt_inputs_digest:
        findings.append(
            TrustFinding(
                "STALE_ARCHITECTURE_EVIDENCE",
                "capture manifest stale vs receipt inputs_digest",
                primary_claim,
            )
        )

    proof = tool_receipt.get("execution_proof_fixture") or {}
    rel = proof.get("path")
    if not rel:
        findings.append(
            TrustFinding("TOOL_EXECUTION_RECEIPT_INVALID", "missing proof fixture path", primary_claim)
        )
        return findings
    rel_str = str(rel).replace("\\", "/")
    if not rel_str.startswith(ALLOWED_TOOL_PROOF_PREFIX):
        findings.append(
            TrustFinding(
                "TOOL_EXECUTION_RECEIPT_INVALID",
                "execution proof fixture outside allowlist",
                primary_claim,
            )
        )
        return findings
    proof_path = (repo_root / rel_str).resolve()
    if not proof_path.is_file():
        findings.append(
            TrustFinding("TOOL_EXECUTION_RECEIPT_INVALID", "proof fixture missing", primary_claim)
        )
        return findings
    if sha256_file(proof_path) != proof.get("content_sha256"):
        findings.append(
            TrustFinding(
                "TOOL_EXECUTION_RECEIPT_INVALID",
                "proof fixture digest mismatch",
                primary_claim,
            )
        )
        return findings

    try:
        fixture = _load_execution_proof_fixture(repo_root, str(rel))
    except ValueError as error:
        findings.append(
            TrustFinding("TOOL_EXECUTION_RECEIPT_INVALID", str(error), primary_claim)
        )
        return findings

    if fixture.get("bound_content_sha256") != manifest.get("content_sha256"):
        findings.append(
            TrustFinding(
                "SOURCE_PROVENANCE_UNTRUSTED_S3",
                "self-consistent hash without fixture-bound provenance",
                primary_claim,
            )
        )

    return findings


def record_independent_review_attestation(
    *,
    change_root: Path,
    repo_root: Path,
    change_id: str,
    design_digest: str,
    inputs_digest: str,
    bound_receipt_sha256: str,
    authority_profile: str,
    reviewer_agent_id: str,
    claim_attestations: list[dict[str, Any]],
    execution_proof_fixture_path: str,
    design_writer_id: str,
) -> dict[str, Any]:
    """Записать independent review attestation (отдельно от self-reported verifier)."""

    if authority_profile not in ALLOWED_REVIEW_AUTHORITY_PROFILES:
        raise ValueError("authority_profile not permitted")
    if reviewer_agent_id not in ALLOWED_REVIEWER_AGENT_IDS:
        raise ValueError("reviewer_agent_id not permitted")
    if reviewer_agent_id == design_writer_id:
        raise ValueError("reviewer must differ from design_writer_id")

    rel_review = execution_proof_fixture_path.replace("\\", "/")
    if not rel_review.startswith(ALLOWED_REVIEW_PROOF_PREFIX):
        raise ValueError("review proof fixture outside allowlist")
    fixture_path = (repo_root / rel_review).resolve()
    if not fixture_path.is_file():
        raise ValueError("review proof fixture missing")

    attestation_path = (change_root / REVIEW_ATTESTATION_PATH).resolve()
    try:
        relative_attestation = attestation_path.relative_to(change_root.resolve())
    except ValueError as exc:
        raise ValueError("attestation path escape") from exc
    # Compare Path components, not POSIX text against Windows backslashes.
    if relative_attestation.parts != REVIEW_ATTESTATION_PATH.parts:
        raise ValueError("attestation path escape")

    payload: dict[str, Any] = {
        "trust_schema_version": 1,
        "receipt_kind": "INDEPENDENT_COMPATIBILITY_REVIEW",
        "change_id": change_id,
        "design_digest": design_digest,
        "inputs_digest": inputs_digest,
        "bound_receipt_sha256": bound_receipt_sha256,
        "review_execution": {
            "authority_profile": authority_profile,
            "reviewer_agent_id": reviewer_agent_id,
        },
        "claim_attestations": claim_attestations,
        "execution_proof_fixture": {
            "path": execution_proof_fixture_path,
            "content_sha256": sha256_file(fixture_path),
        },
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    attestation_path.parent.mkdir(parents=True, exist_ok=True)
    if attestation_path.is_file():
        existing = json.loads(attestation_path.read_text(encoding="utf-8"))
        if existing != payload:
            raise ValueError("review attestation conflict")
        return existing
    tmp = attestation_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(attestation_path)
    return payload


def validate_independent_review_attestation(
    *,
    repo_root: Path,
    change_root: Path,
    receipt: dict[str, Any],
    design_digest: str,
    inputs_digest: str,
    expected_change_id: str,
    trust_policy: TrustPolicy = "production",
) -> list[TrustFinding]:
    """Проверить independent review attestation против текущего receipt."""

    findings: list[TrustFinding] = []
    writer = receipt.get("design_writer_id") or ""
    verifier = receipt.get("verifier") or {}
    summary = receipt.get("summary") or {}

    attestation_path = change_root / REVIEW_ATTESTATION_PATH
    critical_pass = any(
        isinstance(c, dict) and c.get("result") == "PASS" for c in receipt.get("claims") or []
    )
    if trust_policy in ("production", "test_custodian"):
        if attestation_path.is_file() and trust_policy == "production":
            findings.append(
                TrustFinding(
                    TRUSTED_PROVENANCE_UNAVAILABLE,
                    "change-local REVIEW-ATTESTATION.json is not authoritative in production",
                )
            )
        return findings
    if not attestation_path.is_file():
        if trust_policy == "production" and receipt.get("applicability") == "REQUIRED" and critical_pass:
            findings.append(
                TrustFinding(
                    TRUSTED_PROVENANCE_UNAVAILABLE,
                    "production requires custodian review execution proof (unavailable in S3)",
                )
            )
        if any(summary.get(k) for k in ("ready_for_design_freeze", "ready_for_tasks", "ready_for_apply_preflight")):
            findings.append(
                TrustFinding(
                    "INDEPENDENT_REVIEW_AUTHORITY_MISSING",
                    "summary ready without REVIEW-ATTESTATION.json",
                )
            )
        if verifier.get("kind") == "INDEPENDENT_REVIEW_AGENT":
            findings.append(
                TrustFinding(
                    "INDEPENDENT_REVIEW_AUTHORITY_REJECTED",
                    "self-reported INDEPENDENT_REVIEW_AGENT without attestation",
                )
            )
        return findings

    attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
    proof = attestation.get("execution_proof_fixture") or {}
    proof_rel = proof.get("path")
    if trust_policy == "production":
        if proof_rel and _is_test_fixture_path(str(proof_rel)):
            findings.append(
                TrustFinding(
                    TRUSTED_PROVENANCE_UNAVAILABLE,
                    "production cannot trust test review proof fixtures",
                )
            )
        elif not proof_rel or not _production_review_proof_allowed(str(proof_rel)):
            findings.append(
                TrustFinding(
                    TRUSTED_PROVENANCE_UNAVAILABLE,
                    "production requires review execution proof under opsx/trusted-provenance/",
                )
            )
        else:
            proof_path = (repo_root / _normalize_rel_path(str(proof_rel))).resolve()
            if not proof_path.is_file() or sha256_file(proof_path) != proof.get("content_sha256"):
                findings.append(
                    TrustFinding(
                        TRUSTED_PROVENANCE_UNAVAILABLE,
                        "production review execution proof missing or stale",
                    )
                )
        if receipt.get("applicability") == "REQUIRED" and critical_pass:
            findings.append(
                TrustFinding(
                    TRUSTED_PROVENANCE_UNAVAILABLE,
                    "production independent review authority not available in S3 repair scope",
                )
            )
        return findings

    review_exec = attestation.get("review_execution") or {}
    reviewer_id = review_exec.get("reviewer_agent_id") or ""
    authority = review_exec.get("authority_profile") or ""

    if authority not in ALLOWED_REVIEW_AUTHORITY_PROFILES:
        findings.append(
            TrustFinding(
                "INDEPENDENT_REVIEW_AUTHORITY_REJECTED",
                "forged review authority_profile",
            )
        )
    if reviewer_id not in ALLOWED_REVIEWER_AGENT_IDS:
        findings.append(
            TrustFinding(
                "INDEPENDENT_REVIEW_AUTHORITY_REJECTED",
                "forged independent reviewer identity",
            )
        )
    if reviewer_id == writer:
        findings.append(
            TrustFinding("SELF_ATTESTATION_REJECTED", "reviewer equals design_writer_id")
        )
    if verifier.get("id") == writer and verifier.get("kind") == "INDEPENDENT_REVIEW_AGENT":
        findings.append(
            TrustFinding(
                "INDEPENDENT_REVIEW_AUTHORITY_REJECTED",
                "receipt verifier cannot be self-attested review agent",
            )
        )

    receipt_path = change_root / "architecture-evidence" / RECEIPT_FILENAME
    if receipt_path.is_file():
        bound = sha256_file(receipt_path)
        if attestation.get("bound_receipt_sha256") != bound:
            findings.append(
                TrustFinding(
                    "STALE_ARCHITECTURE_EVIDENCE",
                    "review attestation bound_receipt_sha256 stale",
                )
            )

    for field, expected in (
        ("change_id", expected_change_id),
        ("design_digest", design_digest),
        ("inputs_digest", inputs_digest),
    ):
        if attestation.get(field) != expected:
            findings.append(
                TrustFinding(
                    "STALE_ARCHITECTURE_EVIDENCE",
                    f"review attestation {field} mismatch (cross-revision replay)",
                )
            )

    proof = attestation.get("execution_proof_fixture") or {}
    proof_rel = proof.get("path")
    if proof_rel:
        proof_rel_str = str(proof_rel).replace("\\", "/")
        if not proof_rel_str.startswith(ALLOWED_REVIEW_PROOF_PREFIX):
            findings.append(
                TrustFinding(
                    "INDEPENDENT_REVIEW_AUTHORITY_REJECTED",
                    "review proof fixture outside allowlist",
                )
            )
        proof_path = (repo_root / proof_rel_str).resolve()
        if not proof_path.is_file() or sha256_file(proof_path) != proof.get("content_sha256"):
            findings.append(
                TrustFinding(
                    "INDEPENDENT_REVIEW_AUTHORITY_REJECTED",
                    "review execution proof fixture invalid",
                )
            )

    attestations_by_claim = {
        str(item.get("claim_id")): item
        for item in attestation.get("claim_attestations") or []
        if isinstance(item, dict)
    }

    for claim in receipt.get("claims") or []:
        if not isinstance(claim, dict) or claim.get("result") != "PASS":
            continue
        cid = str(claim.get("claim_id"))
        att = attestations_by_claim.get(cid)
        if att is None:
            findings.append(
                TrustFinding(
                    "CLAIM_COMPATIBILITY_UNVERIFIED",
                    f"missing independent attestation for claim {cid}",
                    cid,
                )
            )
            continue
        if att.get("compatibility") != "PASS":
            findings.append(
                TrustFinding(
                    "CLAIM_COMPATIBILITY_UNVERIFIED",
                    f"review attestation not PASS for {cid}",
                    cid,
                )
            )
            continue
        facts = att.get("verified_facts") or {}
        library = claim.get("library") or {}
        if library.get("name") and facts.get("library_name") != library.get("name"):
            findings.append(
                TrustFinding(
                    "CLAIM_COMPATIBILITY_UNVERIFIED",
                    "verified library_name mismatch",
                    cid,
                )
            )
        if library.get("version_claim") and facts.get("version_claim") != library.get(
            "version_claim"
        ):
            findings.append(
                TrustFinding(
                    "LIBRARY_API_INCOMPATIBLE",
                    "review verified version does not match claim",
                    cid,
                )
            )
        api = claim.get("api") or {}
        if api.get("method") and facts.get("api_method") != api.get("method"):
            findings.append(
                TrustFinding(
                    "CLAIM_COMPATIBILITY_UNVERIFIED",
                    "verified api_method mismatch",
                    cid,
                )
            )
        if api.get("path") and facts.get("api_path") != api.get("path"):
            findings.append(
                TrustFinding(
                    "CLAIM_COMPATIBILITY_UNVERIFIED",
                    "verified api_path mismatch",
                    cid,
                )
            )
        required_markers = facts.get("content_markers") or []
        if required_markers:
            for source in claim.get("sources") or []:
                if not isinstance(source, dict) or source.get("kind") != "captured_doc":
                    continue
                capture_id = source.get("capture_id")
                if not capture_id:
                    continue
                from doc_capture import load_capture_manifest  # noqa: WPS433

                manifest = load_capture_manifest(change_root, str(capture_id))
                if manifest is None:
                    continue
                rel = manifest.get("content_path") or ""
                content_path = (change_root / rel).resolve()
                if not content_path.is_file():
                    continue
                text = content_path.read_text(encoding="utf-8")
                for marker in required_markers:
                    if marker not in text:
                        findings.append(
                            TrustFinding(
                                "CLAIM_COMPATIBILITY_UNVERIFIED",
                                f"capture missing required marker {marker}",
                                cid,
                            )
                        )

    return findings


def validate_s3_trust_for_receipt(
    receipt: dict[str, Any],
    *,
    repo_root: Path,
    change_root: Path,
    expected_change_id: str,
    expected_inputs_digest: str,
    design_digest: str,
    s3_strict: bool,
    trust_policy: TrustPolicy = "production",
) -> list[TrustFinding]:
    """S3: provenance + independent review для strict phases."""

    if not s3_strict:
        return []

    findings: list[TrustFinding] = []
    receipt_inputs = str(receipt.get("inputs_digest") or "")

    from doc_capture import load_capture_manifest  # noqa: WPS433

    for claim in receipt.get("claims") or []:
        if not isinstance(claim, dict) or claim.get("result") != "PASS":
            continue
        cid = str(claim.get("claim_id"))
        for source in claim.get("sources") or []:
            if not isinstance(source, dict):
                continue
            if source.get("kind") != "captured_doc":
                continue
            if trust_policy == "production":
                continue
            if trust_policy == "test_custodian":
                continue
            capture_id = str(source.get("capture_id") or "")
            manifest = load_capture_manifest(change_root, capture_id)
            if manifest is None:
                continue
            tool = manifest.get("acquisition_tool")
            if tool == "manual_unattested":
                findings.append(
                    TrustFinding(
                        "SOURCE_PROVENANCE_UNTRUSTED_S3",
                        "manual_unattested cannot satisfy S3 trusted provenance",
                        cid,
                    )
                )
                continue
            findings.extend(
                validate_tool_execution_for_capture(
                    repo_root=repo_root,
                    change_root=change_root,
                    manifest=manifest,
                    expected_change_id=expected_change_id,
                    expected_inputs_digest=expected_inputs_digest,
                    receipt_inputs_digest=receipt_inputs,
                    trust_policy=trust_policy,
                )
            )

    findings.extend(
        validate_independent_review_attestation(
            repo_root=repo_root,
            change_root=change_root,
            receipt=receipt,
            design_digest=design_digest,
            inputs_digest=expected_inputs_digest,
            expected_change_id=expected_change_id,
            trust_policy=trust_policy,
        )
    )

    if trust_policy == "test_fixture" and receipt.get("applicability") == "REQUIRED":
        critical_pass = any(
            isinstance(c, dict) and c.get("result") == "PASS" for c in receipt.get("claims") or []
        )
        if critical_pass and not (change_root / REVIEW_ATTESTATION_PATH).is_file():
            findings.append(
                TrustFinding(
                    "INDEPENDENT_REVIEW_AUTHORITY_MISSING",
                    "REQUIRED change with PASS claims needs review attestation",
                )
            )

    if trust_policy in ("production", "test_custodian"):
        from custodian_trust import validate_production_custodian_trust  # noqa: WPS433

        for cust_f in validate_production_custodian_trust(
            receipt,
            repo_root=repo_root,
            change_root=change_root,
            expected_change_id=expected_change_id,
            expected_inputs_digest=expected_inputs_digest,
            design_digest=design_digest,
            trust_policy=trust_policy,
        ):
            findings.append(
                TrustFinding(cust_f.code, cust_f.detail, getattr(cust_f, "claim_id", None))
            )

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="S3 trust provenance receipts")
    parser.add_argument(
        "command",
        choices=("record-tool-execution", "record-review-attestation"),
    )
    parser.add_argument("--change-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--capture-id", default=None)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--inputs-digest", required=True)
    parser.add_argument("--permitted-tool", default=None)
    parser.add_argument("--issuer-profile", default=None)
    parser.add_argument("--bound-content-sha256", default=None)
    parser.add_argument("--source-uri", default=None)
    parser.add_argument("--publisher", default=None)
    parser.add_argument("--documented-version", default=None)
    parser.add_argument("--execution-proof-fixture", default=None)
    parser.add_argument("--design-digest", default=None)
    parser.add_argument("--bound-receipt-sha256", default=None)
    parser.add_argument("--authority-profile", default=None)
    parser.add_argument("--reviewer-agent-id", default=None)
    parser.add_argument("--design-writer-id", default=None)
    parser.add_argument("--claim-attestation-file", type=Path, default=None)
    args = parser.parse_args(argv)

    change_root = args.change_root.resolve()
    repo_root = args.repo_root.resolve()

    try:
        if args.command == "record-tool-execution":
            manifest = record_tool_execution_receipt(
                change_root=change_root,
                repo_root=repo_root,
                capture_id=args.capture_id or "",
                change_id=args.change_id,
                inputs_digest=args.inputs_digest,
                permitted_tool=args.permitted_tool or "",
                issuer_profile=args.issuer_profile or "",
                bound_content_sha256=args.bound_content_sha256 or "",
                source_uri=args.source_uri or "",
                publisher=args.publisher or "",
                documented_version=args.documented_version or "",
                execution_proof_fixture_path=args.execution_proof_fixture or "",
            )
        else:
            attestations = json.loads(args.claim_attestation_file.read_text(encoding="utf-8"))
            manifest = record_independent_review_attestation(
                change_root=change_root,
                repo_root=repo_root,
                change_id=args.change_id,
                design_digest=args.design_digest or "",
                inputs_digest=args.inputs_digest,
                bound_receipt_sha256=args.bound_receipt_sha256 or "",
                authority_profile=args.authority_profile or "",
                reviewer_agent_id=args.reviewer_agent_id or "",
                claim_attestations=attestations,
                execution_proof_fixture_path=args.execution_proof_fixture or "",
                design_writer_id=args.design_writer_id or "",
            )
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "manifest": manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
