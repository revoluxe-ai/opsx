"""Регрессия ARCHITECTURE_DEPENDENCY_EVIDENCE_V1 validator."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "architecture-evidence" / "architecture_evidence.py"
)
FIXTURES = Path(__file__).resolve().parent / "fixtures"
AUTH = FIXTURES / "authoritative"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("architecture_evidence", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ORACLE = _load()


def _sha(path: Path) -> str:
    return ORACLE.sha256_file(path)


def _base_receipt(**overrides: object) -> dict:
    receipt = {
        "schema_version": 1,
        "contract_id": "ARCHITECTURE_DEPENDENCY_EVIDENCE_V1",
        "change_id": "test-change",
        "applicability": "REQUIRED",
        "design_writer_id": "design-author-1",
        "design_digest": "0" * 64,
        "inputs_digest": "1" * 64,
        "execution_identity": {
            "validator_implementation_id": "architecture-evidence-validator-v1",
            "schema_version": 1,
        },
        "verifier": {"kind": "INDEPENDENT_TOOL", "id": "architecture-evidence-validator-v1"},
        "claims": [],
        "summary": {
            "ready_for_design_freeze": False,
            "ready_for_tasks": False,
            "ready_for_apply_preflight": False,
        },
    }
    receipt.update(overrides)
    return receipt


def _design_with_obligations(tmp_path: Path, *obligations: tuple[str, str]) -> Path:
    lines = ["# Design", "", "ARCHITECTURE_COVERAGE_OBLIGATIONS", ""]
    for oid, category in obligations:
        lines.extend(
            [
                f"- obligation_id: {oid}",
                f"  category: {category}",
                f"  design_anchor: ## External",
                "",
            ]
        )
    path = tmp_path / "design.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _fixture_source(rel: str) -> dict:
    path = AUTH / rel
    return {
        "kind": "fixture_snapshot",
        "path": str(path.relative_to(REPO_ROOT)),
        "content_sha256": _sha(path),
        "revision": "fixture-v1",
    }


def test_api_contract_mismatch_wrong_max_method(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path, ("max-send", "EXTERNAL_API"))
    receipt = _base_receipt(
        design_digest=_sha(design),
        claims=[
            {
                "claim_id": "c1",
                "category": "EXTERNAL_API",
                "design_anchor": "## External",
                "obligation_id": "max-send",
                "result": "PASS",
                "api": {
                    "method": "PATCH",
                    "path": "/messages",
                    "success_semantics": {"status_codes": [200], "body_fields": ["message_id"]},
                    "idempotency_posture": "UNKNOWN",
                },
                "sources": [_fixture_source("max_send_message.json")],
            }
        ],
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    codes = {f.code for f in result.findings}
    assert "API_CONTRACT_MISMATCH" in codes


def test_missing_success_semantics(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path, ("max-send", "EXTERNAL_API"))
    receipt = _base_receipt(
        design_digest=_sha(design),
        claims=[
            {
                "claim_id": "c1",
                "category": "EXTERNAL_API",
                "design_anchor": "## External",
                "obligation_id": "max-send",
                "result": "PASS",
                "api": {"method": "POST", "path": "/messages", "idempotency_posture": "UNKNOWN"},
                "sources": [_fixture_source("max_send_message.json")],
            }
        ],
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "API_CONTRACT_EVIDENCE_MISSING" for f in result.findings)


def test_unknown_idempotency_asserted_proven(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path, ("max-send", "EXTERNAL_API"))
    receipt = _base_receipt(
        design_digest=_sha(design),
        design_policy_assertions={"idempotency_asserted_proven": True},
        claims=[
            {
                "claim_id": "c1",
                "category": "EXTERNAL_API",
                "design_anchor": "## External",
                "obligation_id": "max-send",
                "result": "PASS",
                "api": {
                    "method": "POST",
                    "path": "/messages",
                    "success_semantics": {"status_codes": [200], "body_fields": ["message_id"]},
                    "idempotency_posture": "UNKNOWN",
                },
                "sources": [_fixture_source("max_send_message.json")],
            }
        ],
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "UNKNOWN_IDEMPOTENCY_ASSERTED_AS_PROVEN" for f in result.findings)


def test_unsafe_retry_without_policy(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path)
    receipt = _base_receipt(
        design_digest=_sha(design),
        design_policy_assertions={
            "unsafe_auto_retry_after_timeout": True,
            "safe_retry_policy_documented": False,
        },
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "UNSAFE_RETRY_WITHOUT_POLICY" for f in result.findings)


def test_library_api_incompatible_wrong_doc_version(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path, ("httpx-pin", "LIBRARY"))
    receipt = _base_receipt(
        design_digest=_sha(design),
        claims=[
            {
                "claim_id": "c1",
                "category": "LIBRARY",
                "design_anchor": "## Decisions",
                "obligation_id": "httpx-pin",
                "result": "PASS",
                "library": {"name": "httpx", "version_claim": "2.x"},
                "sources": [_fixture_source("httpx_lockfile.json")],
            }
        ],
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "LIBRARY_API_INCOMPATIBLE" for f in result.findings)


def test_reused_http_client_new_api_missing_claim(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path, ("max-send", "EXTERNAL_API"))
    receipt = _base_receipt(design_digest=_sha(design), claims=[])
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "API_CONTRACT_EVIDENCE_MISSING" for f in result.findings)


def test_self_attestation_rejected(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path)
    receipt = _base_receipt(
        design_digest=_sha(design),
        verifier={"kind": "INDEPENDENT_REVIEW_AGENT", "id": "design-author-1"},
        summary={
            "ready_for_design_freeze": True,
            "ready_for_tasks": False,
            "ready_for_apply_preflight": False,
        },
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "SELF_ATTESTATION_REJECTED" for f in result.findings)


def test_forged_source_digest(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path, ("max-send", "EXTERNAL_API"))
    bad_source = _fixture_source("max_send_message.json")
    bad_source["content_sha256"] = "f" * 64
    receipt = _base_receipt(
        design_digest=_sha(design),
        claims=[
            {
                "claim_id": "c1",
                "category": "EXTERNAL_API",
                "design_anchor": "## External",
                "obligation_id": "max-send",
                "result": "PASS",
                "api": {
                    "method": "POST",
                    "path": "/messages",
                    "success_semantics": {"status_codes": [200], "body_fields": ["message_id"]},
                },
                "sources": [bad_source],
            }
        ],
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "SOURCE_DIGEST_MISMATCH" for f in result.findings)


def test_stale_architecture_evidence_design_digest(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path)
    receipt = _base_receipt(design_digest="a" * 64)
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "STALE_ARCHITECTURE_EVIDENCE" for f in result.findings)


def test_validator_cli_exit_nonzero_when_missing_evidence(tmp_path: Path) -> None:
    receipt_path = tmp_path / "receipt.json"
    receipt_path.write_text(json.dumps(_base_receipt(claims=[])), encoding="utf-8")
    design = _design_with_obligations(tmp_path, ("max-send", "EXTERNAL_API"))
    proc = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "validate",
            "--receipt",
            str(receipt_path),
            "--repo-root",
            str(REPO_ROOT),
            "--design",
            str(design),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False


def test_not_applicable_internal_only(tmp_path: Path) -> None:
    design = tmp_path / "design.md"
    design.write_text("# Internal only\n", encoding="utf-8")
    receipt = _base_receipt(
        applicability="NOT_APPLICABLE",
        scope_rationale="no external API or new library",
        claims=[],
        design_digest=_sha(design),
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert result.ok is True


def test_architecture_invariant_violation(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path, ("write-owner", "ARCHITECTURE_INVARIANT"))
    receipt = _base_receipt(
        design_digest=_sha(design),
        claims=[
            {
                "claim_id": "c1",
                "category": "ARCHITECTURE_INVARIANT",
                "design_anchor": "## Ownership",
                "obligation_id": "write-owner",
                "result": "PASS",
                "architecture_invariant": {
                    "invariant_id": "single-writer",
                    "statement": "wrong statement",
                },
                "sources": [_fixture_source("invariant_violation.json")],
            }
        ],
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "ARCHITECTURE_INVARIANT_VIOLATION" for f in result.findings)


def test_schema_violation_extra_property(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path)
    receipt = _base_receipt(design_digest=_sha(design), forged_field="x")
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "SCHEMA_VIOLATION" for f in result.findings)


def test_forged_independent_tool_verifier_id(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path)
    receipt = _base_receipt(
        design_digest=_sha(design),
        verifier={"kind": "INDEPENDENT_TOOL", "id": "forged-validator"},
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "SELF_ATTESTATION_REJECTED" for f in result.findings)


def test_change_id_mismatch(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path)
    receipt = _base_receipt(design_digest=_sha(design))
    result = ORACLE.validate_receipt(
        receipt,
        repo_root=REPO_ROOT,
        design_path=design,
        expected_change_id="expected-change-id",
    )
    assert any(
        f.code == "STALE_ARCHITECTURE_EVIDENCE" and "change_id" in f.detail
        for f in result.findings
    )


def test_coverage_manifest_missing_when_ta_required(tmp_path: Path) -> None:
    design = tmp_path / "design.md"
    design.write_text(
        "\n".join(
            [
                "# Design",
                "",
                "TECHNOLOGY_ASSESSMENT",
                "REQUIRED: yes",
                "",
            ]
        ),
        encoding="utf-8",
    )
    receipt = _base_receipt(design_digest=_sha(design), claims=[])
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(f.code == "COVERAGE_MANIFEST_MISSING" for f in result.findings)


def test_integration_inventory_obligation_required(tmp_path: Path) -> None:
    design = tmp_path / "design.md"
    design.write_text(
        "\n".join(
            [
                "# Design",
                "",
                "ARCHITECTURE_INTEGRATION_INVENTORY",
                "",
                "- operation_id: inventory-op",
                "  category: EXTERNAL_API",
                "  design_anchor: ## API",
                "",
            ]
        ),
        encoding="utf-8",
    )
    receipt = _base_receipt(design_digest=_sha(design), claims=[])
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert any(
        f.code == "API_CONTRACT_EVIDENCE_MISSING" and "inventory-op" in f.detail
        for f in result.findings
    )


def test_valid_max_claim_passes(tmp_path: Path) -> None:
    design = _design_with_obligations(tmp_path, ("max-send", "EXTERNAL_API"))
    receipt = _base_receipt(
        design_digest=_sha(design),
        claims=[
            {
                "claim_id": "c1",
                "category": "EXTERNAL_API",
                "design_anchor": "## External",
                "obligation_id": "max-send",
                "result": "PASS",
                "api": {
                    "method": "POST",
                    "path": "/messages",
                    "success_semantics": {"status_codes": [200], "body_fields": ["message_id"]},
                    "idempotency_posture": "NOT_IDEMPOTENT",
                },
                "sources": [_fixture_source("max_send_message.json")],
            }
        ],
    )
    result = ORACLE.validate_receipt(receipt, repo_root=REPO_ROOT, design_path=design)
    assert result.ok is True
