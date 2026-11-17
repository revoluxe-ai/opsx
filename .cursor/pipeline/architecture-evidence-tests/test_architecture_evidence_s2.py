"""S2 adversarial gates: discovery, documentation trust, planning integration."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
AE_DIR = Path(__file__).resolve().parents[1] / "architecture-evidence"
AUTH = Path(__file__).resolve().parent / "fixtures" / "authoritative"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GATE = _load_module("planning_gate", AE_DIR / "planning_gate.py")


@pytest.fixture(autouse=True)
def _isolate_dependency_evidence_layer(monkeypatch):
    # This module tests S2 dependency evidence; the mandatory Architecture
    # review gate has its own integration tests in tests/test_architecture_stage.py.
    monkeypatch.setattr(GATE, "_require_architecture_stage", lambda *_: None)

ORACLE = _load_module("architecture_evidence", AE_DIR / "architecture_evidence.py")
DOC = _load_module("doc_verification", AE_DIR / "doc_verification.py")


def _sha(path: Path) -> str:
    return ORACLE.sha256_file(path)


def _write_change(
    tmp_path: Path,
    change_id: str,
    *,
    design_lines: list[str],
    spec_text: str = "",
    receipt: dict | None = None,
    tasks_ready: bool = False,
) -> Path:
    root = tmp_path / "openspec" / "changes" / change_id
    root.mkdir(parents=True)
    (root / "owner-contract.md").write_text("# Owner\n", encoding="utf-8")
    (root / "proposal.md").write_text("# Proposal\n", encoding="utf-8")
    (root / "design.md").write_text("\n".join(design_lines), encoding="utf-8")
    if spec_text:
        spec_dir = root / "specs" / "cap"
        spec_dir.mkdir(parents=True)
        (spec_dir / "spec.md").write_text(spec_text, encoding="utf-8")
    if receipt is not None:
        evidence = root / "architecture-evidence"
        evidence.mkdir()
        (evidence / "ARCH-EVIDENCE.json").write_text(json.dumps(receipt), encoding="utf-8")
    if tasks_ready:
        (root / "tasks.md").write_text("TASKS_STATUS: READY\n", encoding="utf-8")
    return root


def _mirror_schema(repo: Path) -> None:
    src = REPO_ROOT / "opsx_tools/registry/architecture_evidence.schema.json"
    dest = repo / "opsx/registry/architecture_evidence.schema.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())


def _mirror_fixtures(repo: Path) -> None:
    _mirror_schema(repo)
    dest_dir = (
        repo
        / ".cursor/pipeline/architecture-evidence-tests/fixtures/authoritative"
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in AUTH.glob("*.json"):
        (dest_dir / src.name).write_bytes(src.read_bytes())


def _fixture_source(repo: Path, rel: str) -> dict:
    _mirror_fixtures(repo)
    path = repo / ".cursor/pipeline/architecture-evidence-tests/fixtures/authoritative" / rel
    return {
        "kind": "fixture_snapshot",
        "path": str(path.relative_to(repo)),
        "content_sha256": _sha(path),
        "revision": "fixture-v1",
    }


def _write_receipt(change_root: Path, receipt: dict) -> None:
    evidence = change_root / "architecture-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "ARCH-EVIDENCE.json").write_text(json.dumps(receipt), encoding="utf-8")


def _base_receipt(change_id: str, design: Path, change_root: Path, **overrides) -> dict:
    receipt = {
        "schema_version": 1,
        "contract_id": "ARCHITECTURE_DEPENDENCY_EVIDENCE_V1",
        "change_id": change_id,
        "applicability": "REQUIRED",
        "design_writer_id": "author-1",
        "design_digest": _sha(design),
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
    receipt.update(overrides)
    return receipt


def _write_affected_code(repo: Path, change_root: Path, rel: str, content: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    surface = [
        "",
        "ARCHITECTURE_AFFECTED_SURFACE",
        "",
        f"affected_path: {rel}",
        "",
    ]
    design_path = change_root / "design.md"
    design_path.write_text(design_path.read_text(encoding="utf-8") + "\n".join(surface), encoding="utf-8")


def test_code_api_integration_missing_from_inventory(tmp_path: Path) -> None:
    change_id = "s2-max-code"
    repo = tmp_path
    _mirror_schema(repo)
    (repo / "pyproject.toml").write_text('[tool.poetry.dependencies]\nhttpx = "*"\n', encoding="utf-8")
    design = ["# Design", "DESIGN_STATUS: READY", ""]
    change_root = _write_change(repo, change_id, design_lines=design)
    _write_affected_code(
        repo,
        change_root,
        "integrations/max_client.py",
        "import httpx\n\nasync def send() -> None:\n"
        '    client = httpx.AsyncClient(base_url="https://platform-api.max.ru")\n'
        '    await client.post("/messages", json={})\n',
    )
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert outcome.ok is False
    assert any(f.code == "DISCOVERY_OBLIGATION_GAP" for f in outcome.findings)


def test_max_class_wording_not_false_positive(tmp_path: Path) -> None:
    change_id = "s2-max-fp"
    repo = tmp_path
    _mirror_schema(repo)
    (repo / "pyproject.toml").write_text('[tool.poetry.dependencies]\nhttpx = "*"\n', encoding="utf-8")
    design = [
        "# Design",
        "DESIGN_STATUS: READY",
        "Adversary: MAX-class integration mistakes.",
        "",
    ]
    _write_change(repo, change_id, design_lines=design)
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert not any(
        f.code == "DISCOVERY_OBLIGATION_GAP" and "max-send" in f.detail for f in outcome.findings
    )


def test_discovered_library_missing_from_inventory(tmp_path: Path) -> None:
    change_id = "s2-pydantic"
    repo = tmp_path
    (repo / "pyproject.toml").write_text('[tool.poetry.dependencies]\npydantic = "^2"\n', encoding="utf-8")
    _mirror_schema(repo)
    design = ["# Design", "DESIGN_STATUS: READY", ""]
    change_root = _write_change(repo, change_id, design_lines=design)
    _write_affected_code(
        repo,
        change_root,
        "app/models.py",
        "from pydantic import BaseModel\n\nclass M(BaseModel):\n    x: int\n",
    )
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert any(f.code == "DISCOVERY_OBLIGATION_GAP" for f in outcome.findings)


def test_wrong_provider_method_blocks_gate(tmp_path: Path) -> None:
    change_id = "s2-wrong-method"
    design = [
        "# Design",
        "DESIGN_STATUS: READY",
        "",
        "ARCHITECTURE_COVERAGE_OBLIGATIONS",
        "",
        "- obligation_id: max-send",
        "  category: EXTERNAL_API",
        "  design_anchor: ## API",
        "",
    ]
    repo = tmp_path
    _mirror_fixtures(repo)
    change_root = _write_change(repo, change_id, design_lines=design)
    design_path = change_root / "design.md"
    receipt = _base_receipt(
        change_id,
        design_path,
        change_root,
        claims=[
            {
                "claim_id": "c1",
                "category": "EXTERNAL_API",
                "design_anchor": "## API",
                "obligation_id": "max-send",
                "result": "PASS",
                "api": {"method": "PATCH", "path": "/messages", "success_semantics": {"status_codes": [200]}},
                "sources": [_fixture_source(repo, "max_send_message.json")],
            }
        ],
    )
    _write_receipt(change_root, receipt)
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert outcome.ok is False
    assert any(f.code == "API_CONTRACT_MISMATCH" for f in outcome.findings)


def test_wrong_library_doc_version_rejected() -> None:
    claim = {
        "claim_id": "c1",
        "result": "PASS",
        "sources": [
            {
                "kind": "authoritative_doc_digest",
                "revision": "https://docs.example/httpx",
                "content_sha256": "a" * 64,
            }
        ],
    }
    fetcher = DOC.FixtureDocumentFetcher({"https://docs.example/httpx": b"version 1.x docs"})
    findings = DOC.verify_claim_sources(claim, fetcher=fetcher)
    assert any(f.code == "LIBRARY_API_INCOMPATIBLE" for f in findings)


def test_url_without_inspected_source_rejected() -> None:
    claim = {
        "claim_id": "c1",
        "result": "PASS",
        "sources": [{"kind": "authoritative_doc_digest", "revision": "https://example.com/api"}],
    }
    findings = DOC.verify_claim_sources(claim, fetcher=None)
    assert any(f.code == "URL_WITHOUT_INSPECTED_SOURCE" for f in findings)


def test_http_doc_blocked_in_integrated_gate(tmp_path: Path) -> None:
    change_id = "s2-http-doc"
    repo = tmp_path
    _mirror_fixtures(repo)
    design = [
        "# Design",
        "DESIGN_STATUS: READY",
        "",
        "ARCHITECTURE_COVERAGE_OBLIGATIONS",
        "",
        "- obligation_id: max-send",
        "  category: EXTERNAL_API",
        "  design_anchor: ## API",
        "",
    ]
    change_root = _write_change(repo, change_id, design_lines=design)
    design_path = change_root / "design.md"
    receipt = _base_receipt(
        change_id,
        design_path,
        change_root,
        claims=[
            {
                "claim_id": "c1",
                "category": "EXTERNAL_API",
                "design_anchor": "## API",
                "obligation_id": "max-send",
                "result": "PASS",
                "api": {
                    "method": "POST",
                    "path": "/messages",
                    "success_semantics": {"status_codes": [200], "body_fields": ["message_id"]},
                },
                "sources": [
                    {
                        "kind": "authoritative_doc_digest",
                        "revision": "https://docs.example.com/max",
                        "content_sha256": "a" * 64,
                    }
                ],
            }
        ],
    )
    _write_receipt(change_root, receipt)
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert any(f.code == "DOC_ACQUISITION_UNAVAILABLE" for f in outcome.findings)


def test_tasks_ready_without_receipt_blocked(tmp_path: Path) -> None:
    change_id = "s2-tasks"
    repo = tmp_path
    _mirror_schema(repo)
    design = [
        "# Design",
        "",
        "ARCHITECTURE_COVERAGE_OBLIGATIONS",
        "",
        "- obligation_id: op-1",
        "  category: EXTERNAL_API",
        "  design_anchor: ## API",
        "",
    ]
    change_root = _write_change(repo, change_id, design_lines=design, tasks_ready=True)
    outcome = GATE.run_tasks_ready_gate(change_id, repo)
    assert any(f.code == "ARCH_EVIDENCE_MISSING" for f in outcome.findings)


def test_change_id_required(tmp_path: Path) -> None:
    outcome = GATE.run_design_ready_gate("", tmp_path)
    assert any(f.code == "CHANGE_ID_REQUIRED" for f in outcome.findings)


def test_draft_blocked_does_not_promote(tmp_path: Path) -> None:
    change_id = "s2-draft"
    repo = tmp_path
    _mirror_schema(repo)
    design = ["# Design", "DESIGN_STATUS: BLOCKED", "REASON: incomplete", ""]
    _write_change(repo, change_id, design_lines=design)
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert outcome.ok is True


def test_unsupported_idempotency_via_gate(tmp_path: Path) -> None:
    change_id = "s2-idem"
    design = [
        "# Design",
        "DESIGN_STATUS: READY",
        "",
        "ARCHITECTURE_COVERAGE_OBLIGATIONS",
        "",
        "- obligation_id: max-send",
        "  category: EXTERNAL_API",
        "  design_anchor: ## API",
        "",
    ]
    repo = tmp_path
    _mirror_fixtures(repo)
    change_root = _write_change(repo, change_id, design_lines=design)
    design_path = change_root / "design.md"
    receipt = _base_receipt(
        change_id,
        design_path,
        change_root,
        design_policy_assertions={"idempotency_asserted_proven": True},
        claims=[
            {
                "claim_id": "c1",
                "category": "EXTERNAL_API",
                "design_anchor": "## API",
                "obligation_id": "max-send",
                "result": "PASS",
                "api": {
                    "method": "POST",
                    "path": "/messages",
                    "success_semantics": {"status_codes": [200], "body_fields": ["message_id"]},
                    "idempotency_posture": "UNKNOWN",
                },
                "sources": [_fixture_source(repo, "max_send_message.json")],
            }
        ],
    )
    _write_receipt(change_root, receipt)
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert any(f.code == "UNKNOWN_IDEMPOTENCY_ASSERTED_AS_PROVEN" for f in outcome.findings)


def test_mcp_unavailable_not_pass(tmp_path: Path) -> None:
    ACQ = _load_module("doc_acquisition", AE_DIR / "doc_acquisition.py")
    claim = {
        "claim_id": "c1",
        "result": "PASS",
        "sources": [
            {
                "kind": "authoritative_doc_digest",
                "revision": "https://docs.example/missing",
                "content_sha256": "b" * 64,
            }
        ],
    }
    fetcher = ACQ.build_integrated_document_fetcher(tmp_path)
    findings = DOC.verify_claim_sources(claim, fetcher=fetcher, repo_root=tmp_path)
    assert any(f.code == "DOC_ACQUISITION_UNAVAILABLE" for f in findings)


def test_modified_design_invalidates_evidence(tmp_path: Path) -> None:
    change_id = "s2-stale"
    design = [
        "# Design",
        "DESIGN_STATUS: READY",
        "",
        "ARCHITECTURE_COVERAGE_OBLIGATIONS",
        "",
        "- obligation_id: max-send",
        "  category: EXTERNAL_API",
        "  design_anchor: ## API",
        "",
    ]
    repo = tmp_path
    _mirror_fixtures(repo)
    change_root = _write_change(repo, change_id, design_lines=design)
    design_path = change_root / "design.md"
    receipt = _base_receipt(change_id, design_path, change_root, design_digest="f" * 64)
    _write_receipt(change_root, receipt)
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert any(f.code == "STALE_ARCHITECTURE_EVIDENCE" for f in outcome.findings)


def test_forged_reviewer_not_gate_accepted(tmp_path: Path) -> None:
    change_id = "s2-forged-reviewer"
    design = ["# Design", "DESIGN_STATUS: READY", ""]
    repo = tmp_path
    _mirror_fixtures(repo)
    change_root = _write_change(repo, change_id, design_lines=design)
    design_path = change_root / "design.md"
    receipt = _base_receipt(
        change_id,
        design_path,
        change_root,
        applicability="NOT_APPLICABLE",
        scope_rationale="internal",
        verifier={"kind": "INDEPENDENT_REVIEW_AGENT", "id": "trusted-human-42"},
        summary={
            "ready_for_design_freeze": True,
            "ready_for_tasks": False,
            "ready_for_apply_preflight": False,
        },
    )
    _write_receipt(change_root, receipt)
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert any(f.code == "TRUSTED_REVIEW_NOT_ENFORCED_S2" for f in outcome.findings)


def test_internal_not_applicable_without_mcp(tmp_path: Path) -> None:
    change_id = "s2-internal"
    design = ["# Design", "DESIGN_STATUS: READY", "TECHNOLOGY_ASSESSMENT", "REQUIRED: no", ""]
    repo = tmp_path
    _mirror_fixtures(repo)
    change_root = _write_change(repo, change_id, design_lines=design)
    design_path = change_root / "design.md"
    receipt = _base_receipt(
        change_id,
        design_path,
        change_root,
        applicability="NOT_APPLICABLE",
        scope_rationale="control-plane only",
        claims=[],
    )
    _write_receipt(change_root, receipt)
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert outcome.ok is True
