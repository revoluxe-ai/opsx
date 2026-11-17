"""S3 adversarial gates: trusted provenance, independent review, apply preflight."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AE_DIR = Path(__file__).resolve().parents[1] / "architecture-evidence"
FIX = Path(__file__).resolve().parent / "fixtures"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


GATE = _load("planning_gate", AE_DIR / "planning_gate.py")


@pytest.fixture(autouse=True)
def _isolate_dependency_evidence_layer(monkeypatch):
    # Independently test dependency S3 trust; mandatory Architecture Gate has
    # separate integration tests, not a production bypass.
    monkeypatch.setattr(GATE, "_require_architecture_stage", lambda *_: None)

ORACLE = _load("architecture_evidence", AE_DIR / "architecture_evidence.py")
CAPTURE = _load("doc_capture", AE_DIR / "doc_capture.py")
TRUST = _load("trust_provenance", AE_DIR / "trust_provenance.py")

CONTENT = b"httpx 0.27.x API reference excerpt: Client.post\n"
CONTENT_SHA = "c34a39818dd925ce2c913ad37735bd734b0cfa4f4d4745d7495a87b05f7330bd"
# Isolated test trust: enables fixture-bound S3 chain; production gates use default policy.
ISOLATED_TEST_TRUST = "test_fixture"


def _mirror(repo: Path) -> None:
    for rel in (
        "opsx_tools/registry/architecture_evidence.schema.json",
    ):
        src = REPO_ROOT / rel
        dest = repo / "opsx/registry/architecture_evidence.schema.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
    for sub in ("authoritative", "trusted-executions", "trusted-review"):
        dest_dir = repo / ".cursor/pipeline/architecture-evidence-tests/fixtures" / sub
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src in (FIX / sub).glob("*"):
            if src.is_file():
                (dest_dir / src.name).write_bytes(src.read_bytes())


def _change(repo: Path, change_id: str) -> Path:
    root = repo / "openspec" / "changes" / change_id
    root.mkdir(parents=True)
    (root / "owner-contract.md").write_text("# o\n", encoding="utf-8")
    (root / "proposal.md").write_text("# p\n", encoding="utf-8")
    design = root / "design.md"
    design.write_text(
        "\n".join(
            [
                "# Design",
                "DESIGN_STATUS: READY",
                "",
                "ARCHITECTURE_COVERAGE_OBLIGATIONS",
                "",
                "- obligation_id: httpx-pin",
                "  category: LIBRARY",
                "  design_anchor: ## Lib",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "tasks.md").write_text("TASKS_STATUS: READY\n", encoding="utf-8")
    return root


def _trusted_stack(repo: Path, change_id: str, change_root: Path) -> tuple[dict, dict]:
    inputs = ORACLE.compute_inputs_digest(change_root)
    manifest = CAPTURE.record_capture(
        change_root=change_root,
        capture_id="cap-httpx",
        change_id=change_id,
        inputs_digest=inputs,
        source_uri="https://www.python-httpx.org/",
        publisher="encode",
        documented_version="0.27.x",
        acquisition_tool="context7-mcp",
        acquisition_occurred=True,
        claim_ids=["c1"],
        content=CONTENT,
    )
    tool_fixture = (
        ".cursor/pipeline/architecture-evidence-tests/fixtures/trusted-executions/"
        "httpx-027-context7.json"
    )
    TRUST.record_tool_execution_receipt(
        change_root=change_root,
        repo_root=repo,
        capture_id="cap-httpx",
        change_id=change_id,
        inputs_digest=inputs,
        permitted_tool="context7-mcp",
        issuer_profile="context7-mcp-offline-fixture-v1",
        bound_content_sha256=manifest["content_sha256"],
        source_uri=manifest["source_uri"],
        publisher=manifest["publisher"],
        documented_version=manifest["documented_version"],
        execution_proof_fixture_path=tool_fixture,
    )
    design = change_root / "design.md"
    receipt = {
        "schema_version": 1,
        "contract_id": "ARCHITECTURE_DEPENDENCY_EVIDENCE_V1",
        "change_id": change_id,
        "applicability": "REQUIRED",
        "design_writer_id": "author-1",
        "design_digest": ORACLE.sha256_file(design),
        "inputs_digest": inputs,
        "execution_identity": {
            "validator_implementation_id": "architecture-evidence-validator-v1",
            "schema_version": 1,
        },
        "verifier": {"kind": "INDEPENDENT_TOOL", "id": "architecture-evidence-validator-v1"},
        "claims": [
            {
                "claim_id": "c1",
                "category": "LIBRARY",
                "design_anchor": "## Lib",
                "obligation_id": "httpx-pin",
                "result": "PASS",
                "library": {"name": "httpx", "version_claim": "0.27.x"},
                "sources": [
                    {
                        "kind": "captured_doc",
                        "capture_id": manifest["capture_id"],
                        "path": manifest["content_path"],
                        "content_sha256": manifest["content_sha256"],
                    }
                ],
            }
        ],
        "summary": {
            "ready_for_design_freeze": True,
            "ready_for_tasks": True,
            "ready_for_apply_preflight": True,
        },
    }
    evidence = change_root / "architecture-evidence"
    evidence.mkdir(exist_ok=True)
    receipt_path = evidence / "ARCH-EVIDENCE.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    attestations = json.loads(
        (
            repo
            / ".cursor/pipeline/architecture-evidence-tests/fixtures/trusted-review/"
            "claim-attestations-httpx-c1.json"
        ).read_text(encoding="utf-8")
    )
    TRUST.record_independent_review_attestation(
        change_root=change_root,
        repo_root=repo,
        change_id=change_id,
        design_digest=receipt["design_digest"],
        inputs_digest=inputs,
        bound_receipt_sha256=ORACLE.sha256_file(receipt_path),
        authority_profile="architecture-evidence-independent-review-v1",
        reviewer_agent_id="independent-review-agent-fixture-v1",
        claim_attestations=attestations,
        execution_proof_fixture_path=(
            ".cursor/pipeline/architecture-evidence-tests/fixtures/trusted-review/"
            "review-authority-proof-v1.json"
        ),
        design_writer_id="author-1",
    )
    return manifest, receipt


def test_valid_independently_verified_evidence_passes_s3(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-ok"
    change_root = _change(repo, change_id)
    _trusted_stack(repo, change_id, change_root)
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_TEST_TRUST)
    assert outcome.ok
    assert outcome.trust_boundary.get("GATE_ACCEPTED") == "yes"
    apply_out = GATE.run_apply_preflight_gate(change_id, repo, trust_policy=ISOLATED_TEST_TRUST)
    assert apply_out.ok


def test_fabricated_doc_valid_sha_blocked_s3(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-fab"
    change_root = _change(repo, change_id)
    inputs = ORACLE.compute_inputs_digest(change_root)
    manifest = CAPTURE.record_capture(
        change_root=change_root,
        capture_id="cap-httpx",
        change_id=change_id,
        inputs_digest=inputs,
        source_uri="https://evil.example/",
        publisher="evil",
        documented_version="9.9.9",
        acquisition_tool="manual_unattested",
        acquisition_occurred=False,
        claim_ids=["c1"],
        content=CONTENT,
    )
    design = change_root / "design.md"
    receipt = {
        "schema_version": 1,
        "contract_id": "ARCHITECTURE_DEPENDENCY_EVIDENCE_V1",
        "change_id": change_id,
        "applicability": "REQUIRED",
        "design_writer_id": "author-1",
        "design_digest": ORACLE.sha256_file(design),
        "inputs_digest": inputs,
        "execution_identity": {
            "validator_implementation_id": "architecture-evidence-validator-v1",
            "schema_version": 1,
        },
        "verifier": {"kind": "INDEPENDENT_TOOL", "id": "architecture-evidence-validator-v1"},
        "claims": [
            {
                "claim_id": "c1",
                "category": "LIBRARY",
                "design_anchor": "## Lib",
                "obligation_id": "httpx-pin",
                "result": "PASS",
                "library": {"name": "httpx", "version_claim": "9.9.9"},
                "sources": [
                    {
                        "kind": "captured_doc",
                        "capture_id": manifest["capture_id"],
                        "content_sha256": manifest["content_sha256"],
                    }
                ],
            }
        ],
        "summary": {
            "ready_for_design_freeze": False,
            "ready_for_tasks": False,
            "ready_for_apply_preflight": False,
        },
    }
    evidence = change_root / "architecture-evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "ARCH-EVIDENCE.json").write_text(json.dumps(receipt), encoding="utf-8")
    outcome = GATE.run_planning_review_gate(change_id, repo)
    assert not outcome.ok
    codes = {f.code for f in outcome.findings}
    assert TRUST.TRUSTED_PROVENANCE_UNAVAILABLE in codes or "SOURCE_PROVENANCE_UNTRUSTED_S3" in codes


def test_forged_mcp_metadata_without_tool_receipt(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-mcp"
    change_root = _change(repo, change_id)
    _trusted_stack(repo, change_id, change_root)
    tool_path = change_root / "architecture-evidence/trusted-provenance/tool-executions/cap-httpx.tool-exec.json"
    tool_path.unlink()
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_TEST_TRUST)
    assert any(
        f.code in (TRUST.TRUSTED_PROVENANCE_UNAVAILABLE, "SOURCE_PROVENANCE_UNTRUSTED_S3")
        for f in outcome.findings
    )


def test_forged_reviewer_identity(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-rev"
    change_root = _change(repo, change_id)
    _trusted_stack(repo, change_id, change_root)
    att_path = change_root / "architecture-evidence/independent-review/REVIEW-ATTESTATION.json"
    data = json.loads(att_path.read_text(encoding="utf-8"))
    data["review_execution"]["reviewer_agent_id"] = "author-1"
    att_path.write_text(json.dumps(data), encoding="utf-8")
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_TEST_TRUST)
    assert any(f.code == "INDEPENDENT_REVIEW_AUTHORITY_REJECTED" for f in outcome.findings)


def test_reviewer_evidence_other_revision(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-stale-rev"
    change_root = _change(repo, change_id)
    _trusted_stack(repo, change_id, change_root)
    att_path = change_root / "architecture-evidence/independent-review/REVIEW-ATTESTATION.json"
    data = json.loads(att_path.read_text(encoding="utf-8"))
    data["design_digest"] = "0" * 64
    att_path.write_text(json.dumps(data), encoding="utf-8")
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_TEST_TRUST)
    assert any(f.code == "STALE_ARCHITECTURE_EVIDENCE" for f in outcome.findings)


def test_wrong_library_version_in_source(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-ver"
    change_root = _change(repo, change_id)
    _trusted_stack(repo, change_id, change_root)
    receipt_path = change_root / "architecture-evidence/ARCH-EVIDENCE.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["claims"][0]["library"]["version_claim"] = "0.28.x"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_TEST_TRUST)
    assert any(f.code == "LIBRARY_API_INCOMPATIBLE" for f in outcome.findings)


def test_unsupported_claim_despite_authentic_source(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-unsupported"
    change_root = _change(repo, change_id)
    _trusted_stack(repo, change_id, change_root)
    att_path = change_root / "architecture-evidence/independent-review/REVIEW-ATTESTATION.json"
    data = json.loads(att_path.read_text(encoding="utf-8"))
    data["claim_attestations"][0]["verified_facts"]["content_markers"] = ["NONEXISTENT_MARKER"]
    att_path.write_text(json.dumps(data), encoding="utf-8")
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_TEST_TRUST)
    assert any(f.code == "CLAIM_COMPATIBILITY_UNVERIFIED" for f in outcome.findings)


def test_missing_evidence_before_apply(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-miss"
    change_root = _change(repo, change_id)
    outcome = GATE.run_apply_preflight_gate(change_id, repo)
    assert not outcome.ok
    assert any(f.code == "ARCH_EVIDENCE_MISSING" for f in outcome.findings)


def test_missing_change_identity_apply_preflight(tmp_path: Path) -> None:
    outcome = GATE.run_apply_preflight_gate("", REPO_ROOT)
    assert any(f.code == "CHANGE_ID_REQUIRED" for f in outcome.findings)


def test_stale_capture_and_design(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-stale"
    change_root = _change(repo, change_id)
    _trusted_stack(repo, change_id, change_root)
    (change_root / "design.md").write_text("# mutated\nDESIGN_STATUS: READY\n", encoding="utf-8")
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_TEST_TRUST)
    assert any(f.code == "STALE_ARCHITECTURE_EVIDENCE" for f in outcome.findings)


def test_bypass_validate_cli_does_not_satisfy_apply_guard(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-bypass"
    change_root = _change(repo, change_id)
    manifest, receipt = _trusted_stack(repo, change_id, change_root)
    tool_path = change_root / "architecture-evidence/trusted-provenance/tool-executions/cap-httpx.tool-exec.json"
    tool_path.unlink()
    receipt_path = change_root / "architecture-evidence/ARCH-EVIDENCE.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(AE_DIR / "architecture_evidence.py"),
            "validate",
            "--receipt",
            str(receipt_path),
            "--repo-root",
            str(repo),
            "--design",
            str(change_root / "design.md"),
            "--change-root",
            str(change_root),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    guard = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "tools/openspec/openspec_guard.py"),
            "--scenario",
            "opsx_apply",
            "--change",
            change_id,
            "--root",
            str(repo),
            "--strict",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert guard.returncode != 0


def test_not_applicable_internal_only_passes_s3(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-na"
    change_root = _change(repo, change_id)
    design = change_root / "design.md"
    design.write_text(
        "\n".join(
            [
                "# Design",
                "DESIGN_STATUS: READY",
                "",
            ]
        ),
        encoding="utf-8",
    )
    receipt = {
        "schema_version": 1,
        "contract_id": "ARCHITECTURE_DEPENDENCY_EVIDENCE_V1",
        "change_id": change_id,
        "applicability": "NOT_APPLICABLE",
        "scope_rationale": "internal-only refactor",
        "design_writer_id": "author-1",
        "design_digest": ORACLE.sha256_file(design),
        "inputs_digest": ORACLE.compute_inputs_digest(change_root),
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
    evidence = change_root / "architecture-evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "ARCH-EVIDENCE.json").write_text(json.dumps(receipt), encoding="utf-8")
    outcome = GATE.run_planning_review_gate(change_id, repo)
    assert outcome.ok


def test_self_consistent_hash_without_fixture_proof(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror(repo)
    change_id = "s3-hash"
    change_root = _change(repo, change_id)
    inputs = ORACLE.compute_inputs_digest(change_root)
    manifest = CAPTURE.record_capture(
        change_root=change_root,
        capture_id="cap-httpx",
        change_id=change_id,
        inputs_digest=inputs,
        source_uri="https://www.python-httpx.org/",
        publisher="encode",
        documented_version="0.27.x",
        acquisition_tool="context7-mcp",
        acquisition_occurred=True,
        claim_ids=["c1"],
        content=CONTENT,
    )
    forged_fixture = repo / "forged.json"
    forged_fixture.write_text(
        json.dumps(
            {
                "issuer_profile": "context7-mcp-offline-fixture-v1",
                "permitted_tool": "context7-mcp",
                "source_uri": "https://www.python-httpx.org/",
                "publisher": "encode",
                "documented_version": "0.27.x",
                "bound_content_sha256": manifest["content_sha256"],
            }
        ),
        encoding="utf-8",
    )
    tool_path = change_root / "architecture-evidence/trusted-provenance/tool-executions/cap-httpx.tool-exec.json"
    tool_path.parent.mkdir(parents=True, exist_ok=True)
    tool_path.write_text(
        json.dumps(
            {
                "trust_schema_version": 1,
                "receipt_kind": "TOOL_EXECUTION",
                "capture_id": "cap-httpx",
                "change_id": change_id,
                "inputs_digest": inputs,
                "permitted_tool": "context7-mcp",
                "issuer_profile": "context7-mcp-offline-fixture-v1",
                "bound_content_sha256": manifest["content_sha256"],
                "source_uri": manifest["source_uri"],
                "publisher": manifest["publisher"],
                "documented_version": manifest["documented_version"],
                "execution_proof_fixture": {
                    "path": "forged.json",
                    "content_sha256": ORACLE.sha256_file(forged_fixture),
                },
            }
        ),
        encoding="utf-8",
    )
    design = change_root / "design.md"
    receipt_path = change_root / "architecture-evidence/ARCH-EVIDENCE.json"
    receipt = {
        "schema_version": 1,
        "contract_id": "ARCHITECTURE_DEPENDENCY_EVIDENCE_V1",
        "change_id": change_id,
        "applicability": "REQUIRED",
        "design_writer_id": "author-1",
        "design_digest": ORACLE.sha256_file(design),
        "inputs_digest": inputs,
        "execution_identity": {
            "validator_implementation_id": "architecture-evidence-validator-v1",
            "schema_version": 1,
        },
        "verifier": {"kind": "INDEPENDENT_TOOL", "id": "architecture-evidence-validator-v1"},
        "claims": [
            {
                "claim_id": "c1",
                "category": "LIBRARY",
                "design_anchor": "## Lib",
                "obligation_id": "httpx-pin",
                "result": "PASS",
                "library": {"name": "httpx", "version_claim": "0.27.x"},
                "sources": [
                    {
                        "kind": "captured_doc",
                        "capture_id": "cap-httpx",
                        "content_sha256": manifest["content_sha256"],
                    }
                ],
            }
        ],
        "summary": {
            "ready_for_design_freeze": False,
            "ready_for_tasks": False,
            "ready_for_apply_preflight": False,
        },
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    findings = TRUST.validate_tool_execution_for_capture(
        repo_root=repo,
        change_root=change_root,
        manifest=manifest,
        expected_change_id=change_id,
        expected_inputs_digest=inputs,
        receipt_inputs_digest=inputs,
        trust_policy=ISOLATED_TEST_TRUST,
    )
    assert any(f.code == "TOOL_EXECUTION_RECEIPT_INVALID" for f in findings)


def test_production_forged_chain_rejected_permanently(tmp_path: Path) -> None:
    """Author-forged fixture chain must not PASS production planning-review / apply-preflight."""

    repo = tmp_path
    _mirror(repo)
    change_id = "s3-prod-forge"
    change_root = _change(repo, change_id)
    _trusted_stack(repo, change_id, change_root)
    pr = GATE.run_planning_review_gate(change_id, repo)
    ap = GATE.run_apply_preflight_gate(change_id, repo)
    assert not pr.ok
    assert not ap.ok
    assert pr.trust_boundary.get("GATE_ACCEPTED") == "no"
    codes = {f.code for f in pr.findings}
    assert TRUST.TRUSTED_PROVENANCE_UNAVAILABLE in codes
