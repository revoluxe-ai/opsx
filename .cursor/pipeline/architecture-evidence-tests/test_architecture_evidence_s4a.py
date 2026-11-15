"""S4A custodian trust policy, adversarial and isolated positive tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AE_DIR = Path(__file__).resolve().parents[1] / "architecture-evidence"
FIX = Path(__file__).resolve().parent / "fixtures"

CONTENT = b"httpx 0.27.x API reference excerpt: Client.post\n"
ISOLATED_CUSTODIAN = "test_custodian"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


GATE = _load("planning_gate_s4a", AE_DIR / "planning_gate.py")
ORACLE = _load("architecture_evidence_s4a", AE_DIR / "architecture_evidence.py")
CAPTURE = _load("doc_capture_s4a", AE_DIR / "doc_capture.py")
CUST = _load("custodian_trust_s4a", AE_DIR / "custodian_trust.py")
POLICY = _load("trusted_policy_s4a", AE_DIR / "trusted_policy_v1.py")


def _mirror_schema(repo: Path) -> None:
    dest = repo / "opsx/registry/architecture_evidence.schema.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes((REPO_ROOT / "opsx/registry/architecture_evidence.schema.json").read_bytes())


def _change(repo: Path, change_id: str, *, obligations: bool = True) -> Path:
    root = repo / "openspec/changes" / change_id
    root.mkdir(parents=True)
    (root / "owner-contract.md").write_text("# o\n", encoding="utf-8")
    (root / "proposal.md").write_text("# p\n", encoding="utf-8")
    lines = ["# Design", "DESIGN_STATUS: READY", ""]
    if obligations:
        lines += [
            "ARCHITECTURE_COVERAGE_OBLIGATIONS",
            "",
            "- obligation_id: httpx-pin",
            "  category: LIBRARY",
            "  design_anchor: ## Lib",
            "",
        ]
    (root / "design.md").write_text("\n".join(lines), encoding="utf-8")
    (root / "tasks.md").write_text("TASKS_STATUS: READY\n", encoding="utf-8")
    return root


def _issuer(workflow_path: str, actor: str = "custodian-bot", run_id: str = "999001") -> dict:
    return {
        "kind": "github_actions_workflow",
        "repository": POLICY.CANONICAL_REPOSITORY,
        "workflow_path": workflow_path,
        "workflow_ref": "refs/heads/main",
        "run_id": run_id,
        "run_attempt": 1,
        "actor": actor,
    }


def _write_custodian_stack(
    repo: Path,
    change_id: str,
    change_root: Path,
    *,
    acquisition_actor: str = "acquisition-bot",
    review_actor: str = "review-bot",
) -> dict:
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
    evidence = change_root / "architecture-evidence"
    evidence.mkdir(exist_ok=True)
    receipt_path = evidence / "ARCH-EVIDENCE.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    bound = ORACLE.sha256_file(receipt_path)
    acq_path = CUST.acquisition_custodian_path(repo, change_id, "cap-httpx")
    acq_path.parent.mkdir(parents=True, exist_ok=True)
    acq_path.write_text(
        json.dumps(
            {
                "custodian_schema_version": 1,
                "kind": "CUSTODIAN_ACQUISITION",
                "issuer": _issuer(POLICY.TEST_CUSTODIAN_ACQUISITION_WORKFLOW, acquisition_actor),
                "execution_identity": {
                    "trust_policy_implementation_id": POLICY.TRUST_POLICY_IMPLEMENTATION_ID
                },
                "change_id": change_id,
                "inputs_digest": inputs,
                "design_digest": receipt["design_digest"],
                "bound_receipt_sha256": bound,
                "capture_id": "cap-httpx",
                "content_sha256": manifest["content_sha256"],
                "source_uri": manifest["source_uri"],
                "publisher": manifest["publisher"],
                "documented_version": manifest["documented_version"],
                "claim_ids": ["c1"],
                "issued_at": "2026-01-01T00:00:00Z",
                "acquisition_findings": ["integrity_ok"],
            }
        ),
        encoding="utf-8",
    )
    rev_path = CUST.review_custodian_path(repo, change_id)
    rev_path.parent.mkdir(parents=True, exist_ok=True)
    rev_path.write_text(
        json.dumps(
            {
                "custodian_schema_version": 1,
                "kind": "CUSTODIAN_COMPATIBILITY_REVIEW",
                "issuer": _issuer(POLICY.TEST_CUSTODIAN_REVIEW_WORKFLOW, review_actor),
                "execution_identity": {
                    "trust_policy_implementation_id": POLICY.TRUST_POLICY_IMPLEMENTATION_ID
                },
                "change_id": change_id,
                "inputs_digest": inputs,
                "design_digest": receipt["design_digest"],
                "bound_receipt_sha256": bound,
                "claim_reviews": [
                    {
                        "claim_id": "c1",
                        "compatibility": "PASS",
                        "verified_facts": {
                            "library_name": "httpx",
                            "version_claim": "0.27.x",
                            "content_markers": ["Client.post", "0.27.x"],
                        },
                        "findings": ["reviewed_offline_fixture"],
                    }
                ],
                "issued_at": "2026-01-01T00:00:01Z",
            }
        ),
        encoding="utf-8",
    )
    return receipt


def test_isolated_test_custodian_positive(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "s4a-ok"
    change_root = _change(repo, change_id)
    _write_custodian_stack(repo, change_id, change_root)
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_CUSTODIAN)
    assert outcome.ok
    prod = GATE.run_planning_review_gate(change_id, repo, trust_policy="production")
    assert not prod.ok


def test_production_rejects_author_custodian_json(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "s4a-prod-forge"
    change_root = _change(repo, change_id)
    _write_custodian_stack(
        repo,
        change_id,
        change_root,
        acquisition_actor="author-1",
        review_actor="author-1",
    )
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy="production")
    assert not outcome.ok
    codes = {f.code for f in outcome.findings}
    assert CUST.TRUSTED_PROVENANCE_UNAVAILABLE in codes or CUST.CUSTODIAN_ISSUER_REJECTED in codes


def test_forged_bc_id_in_custodian_rejected(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "s4a-bc"
    change_root = _change(repo, change_id)
    _write_custodian_stack(repo, change_id, change_root)
    acq = CUST.acquisition_custodian_path(repo, change_id, "cap-httpx")
    data = json.loads(acq.read_text(encoding="utf-8"))
    data["execution_identity"]["cursor_bc_id"] = "bc-fake-independent"
    acq.write_text(json.dumps(data), encoding="utf-8")
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_CUSTODIAN)
    assert any(f.code == CUST.CUSTODIAN_RECEIPT_INVALID for f in outcome.findings)


def test_same_actor_acquisition_and_review_rejected(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "s4a-self-review"
    change_root = _change(repo, change_id)
    _write_custodian_stack(repo, change_id, change_root, acquisition_actor="bot-a", review_actor="bot-a")
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_CUSTODIAN)
    assert any(f.code == CUST.CUSTODIAN_ISSUER_REJECTED for f in outcome.findings)


def test_stale_custodian_revision_rejected(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "s4a-stale"
    change_root = _change(repo, change_id)
    _write_custodian_stack(repo, change_id, change_root)
    rev = CUST.review_custodian_path(repo, change_id)
    data = json.loads(rev.read_text(encoding="utf-8"))
    data["inputs_digest"] = "0" * 64
    rev.write_text(json.dumps(data), encoding="utf-8")
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_CUSTODIAN)
    assert any(f.code == CUST.CUSTODIAN_RECEIPT_INVALID for f in outcome.findings)


def test_false_not_applicable_blocked(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "s4a-false-na"
    change_root = _change(repo, change_id, obligations=True)
    design = change_root / "design.md"
    receipt = {
        "schema_version": 1,
        "contract_id": "ARCHITECTURE_DEPENDENCY_EVIDENCE_V1",
        "change_id": change_id,
        "applicability": "NOT_APPLICABLE",
        "scope_rationale": "claimed internal",
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
    assert any(f.code == "FALSE_NOT_APPLICABLE_REJECTED" for f in outcome.findings)


def test_pr_controlled_workflow_path_rejected(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "s4a-evil-wf"
    change_root = _change(repo, change_id)
    _write_custodian_stack(repo, change_id, change_root)
    acq = CUST.acquisition_custodian_path(repo, change_id, "cap-httpx")
    data = json.loads(acq.read_text(encoding="utf-8"))
    data["issuer"]["workflow_path"] = ".github/workflows/evil-forged.yml"
    acq.write_text(json.dumps(data), encoding="utf-8")
    outcome = GATE.run_planning_review_gate(change_id, repo, trust_policy=ISOLATED_CUSTODIAN)
    assert any(f.code == CUST.CUSTODIAN_ISSUER_REJECTED for f in outcome.findings)


def test_trust_policy_modes_not_interchangeable(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "s4a-modes"
    change_root = _change(repo, change_id)
    _write_custodian_stack(repo, change_id, change_root)
    fixture_gate = GATE.run_planning_review_gate(change_id, repo, trust_policy="test_fixture")
    assert not fixture_gate.ok
