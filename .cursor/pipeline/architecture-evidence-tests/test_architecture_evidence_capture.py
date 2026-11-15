"""Adversarial tests: change-local documentation capture (S2)."""

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


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


CAPTURE = _load("doc_capture", AE_DIR / "doc_capture.py")
GATE = _load("planning_gate", AE_DIR / "planning_gate.py")
ORACLE = _load("architecture_evidence", AE_DIR / "architecture_evidence.py")


def _mirror_schema(repo: Path) -> None:
    src = REPO_ROOT / "opsx/registry/architecture_evidence.schema.json"
    dest = repo / "opsx/registry/architecture_evidence.schema.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())


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
    return root


def _record_valid(change_root: Path, change_id: str, content: bytes, **overrides: object) -> dict:
    inputs = ORACLE.compute_inputs_digest(change_root)
    kwargs = {
        "change_root": change_root,
        "capture_id": "cap-httpx",
        "change_id": change_id,
        "inputs_digest": inputs,
        "source_uri": "https://www.python-httpx.org/",
        "publisher": "encode",
        "documented_version": "0.27.x",
        "acquisition_tool": "context7-mcp",
        "acquisition_occurred": True,
        "claim_ids": ["c1"],
        "content": content,
    }
    kwargs.update(overrides)
    return CAPTURE.record_capture(**kwargs)


def _receipt(change_root: Path, change_id: str, manifest: dict, **claim_overrides) -> dict:
    design = change_root / "design.md"
    return {
        "schema_version": 1,
        "contract_id": "ARCHITECTURE_DEPENDENCY_EVIDENCE_V1",
        "change_id": change_id,
        "applicability": "REQUIRED",
        "design_writer_id": "author",
        "design_digest": ORACLE.sha256_file(design),
        "inputs_digest": manifest["inputs_digest"],
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
                **claim_overrides,
            }
        ],
        "summary": {
            "ready_for_design_freeze": False,
            "ready_for_tasks": False,
            "ready_for_apply_preflight": False,
        },
    }


def test_valid_agent_acquired_capture_passes_gate(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "cap-ok"
    change_root = _change(repo, change_id)
    content = b"httpx 0.27.x API reference excerpt: Client.post\n"
    manifest = _record_valid(change_root, change_id, content)
    receipt = _receipt(change_root, change_id, manifest)
    evidence = change_root / "architecture-evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "ARCH-EVIDENCE.json").write_text(json.dumps(receipt), encoding="utf-8")
    outcome = GATE.run_design_ready_gate(change_id, repo)
    assert not any(f.code == "PROVENANCE_NOT_TRUSTED_S2" for f in outcome.findings)
    assert not any(f.code == "DOC_CAPTURE_MISSING" for f in outcome.findings)


def test_wrong_version_blocked(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "cap-ver"
    change_root = _change(repo, change_id)
    manifest = _record_valid(change_root, change_id, b"doc for 0.27.x\n", documented_version="0.27.x")
    receipt = _receipt(change_root, change_id, manifest)
    receipt["claims"][0]["library"]["version_claim"] = "0.28.x"
    findings = CAPTURE.validate_captures_for_receipt(
        receipt,
        change_root=change_root,
        expected_change_id=change_id,
        expected_inputs_digest=manifest["inputs_digest"],
    )
    assert any(f.code == "LIBRARY_API_INCOMPATIBLE" for f in findings)


def test_conflicting_digest_on_replay(tmp_path: Path) -> None:
    change_root = _change(tmp_path, "cap-conflict")
    _record_valid(change_root, "cap-conflict", b"first\n")
    with pytest.raises(ValueError, match="digest mismatch"):
        _record_valid(change_root, "cap-conflict", b"second\n")


def test_invented_acquisition_metadata_rejected_at_record(tmp_path: Path) -> None:
    change_root = _change(tmp_path, "cap-meta")
    with pytest.raises(ValueError, match="acquisition_occurred"):
        _record_valid(
            change_root,
            "cap-meta",
            b"x\n",
            acquisition_tool="context7-mcp",
            acquisition_occurred=False,
        )


def test_missing_documentation_blocked(tmp_path: Path) -> None:
    repo = tmp_path
    _mirror_schema(repo)
    change_id = "cap-miss"
    change_root = _change(repo, change_id)
    design = change_root / "design.md"
    receipt = {
        "schema_version": 1,
        "contract_id": "ARCHITECTURE_DEPENDENCY_EVIDENCE_V1",
        "change_id": change_id,
        "applicability": "REQUIRED",
        "design_writer_id": "a",
        "design_digest": ORACLE.sha256_file(design),
        "inputs_digest": ORACLE.compute_inputs_digest(change_root),
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
                        "capture_id": "missing-cap",
                        "content_sha256": "a" * 64,
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
    findings = CAPTURE.validate_captures_for_receipt(
        receipt,
        change_root=change_root,
        expected_change_id=change_id,
        expected_inputs_digest=receipt["inputs_digest"],
    )
    assert any(f.code == "DOC_CAPTURE_MISSING" for f in findings)


def test_stale_capture_inputs_digest(tmp_path: Path) -> None:
    change_root = _change(tmp_path, "cap-stale")
    manifest = _record_valid(change_root, "cap-stale", b"doc\n")
    receipt = _receipt(change_root, "cap-stale", manifest)
    receipt["inputs_digest"] = "f" * 64
    findings = CAPTURE.validate_captures_for_receipt(
        receipt,
        change_root=change_root,
        expected_change_id="cap-stale",
        expected_inputs_digest=ORACLE.compute_inputs_digest(change_root),
    )
    assert any(f.code == "STALE_ARCHITECTURE_EVIDENCE" for f in findings)


def test_path_traversal_capture_id_rejected(tmp_path: Path) -> None:
    change_root = _change(tmp_path, "cap-path")
    with pytest.raises(ValueError):
        CAPTURE.record_capture(
            change_root=change_root,
            capture_id="../evil",
            change_id="cap-path",
            inputs_digest=ORACLE.compute_inputs_digest(change_root),
            source_uri="https://example.com",
            publisher="x",
            documented_version="1",
            acquisition_tool="manual_unattested",
            acquisition_occurred=False,
            claim_ids=["c1"],
            content=b"x",
        )


def test_fabricated_self_consistent_hash_not_trusted_provenance(tmp_path: Path) -> None:
    _mirror_schema(tmp_path)
    change_root = _change(tmp_path, "cap-fab")
    manifest = _record_valid(
        change_root,
        "cap-fab",
        b"author invented content\n",
        acquisition_tool="manual_unattested",
        acquisition_occurred=False,
    )
    receipt = _receipt(change_root, "cap-fab", manifest)
    findings = CAPTURE.validate_captures_for_receipt(
        receipt,
        change_root=change_root,
        expected_change_id="cap-fab",
        expected_inputs_digest=manifest["inputs_digest"],
    )
    assert any(f.code == "PROVENANCE_NOT_TRUSTED_S2" for f in findings)
    evidence = change_root / "architecture-evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "ARCH-EVIDENCE.json").write_text(json.dumps(receipt), encoding="utf-8")
    outcome = GATE.run_design_ready_gate("cap-fab", tmp_path)
    assert any(f.code == "PROVENANCE_NOT_TRUSTED_S2" for f in outcome.findings)


def test_capture_replay_other_change_identity(tmp_path: Path) -> None:
    change_root = _change(tmp_path, "cap-a")
    manifest = _record_valid(change_root, "cap-a", b"doc\n")
    receipt = _receipt(change_root, "cap-wrong", manifest)
    findings = CAPTURE.validate_captures_for_receipt(
        receipt,
        change_root=change_root,
        expected_change_id="cap-wrong",
        expected_inputs_digest=manifest["inputs_digest"],
    )
    assert any(f.code == "DOC_CAPTURE_IDENTITY_MISMATCH" for f in findings)
