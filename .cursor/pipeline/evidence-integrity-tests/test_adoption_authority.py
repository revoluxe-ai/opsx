"""Приёмка S-EV2: audit, authority v2 и fail-closed gates. Фикстуры синтетические."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "evidence-integrity" / "adoption_authority.py"
REPO = Path(__file__).resolve().parents[3]
IMPLEMENT = REPO / "opsx_tools" / "opsx-apply-implement.mjs"
GREEN = REPO / "opsx_tools" / "opsx-apply-green.mjs"
TELEGRAM_IMPLEMENTATION = (
    REPO / "openspec/changes/telegram-lead-form/apply-implement/SLICE-001/IMPLEMENTATION.json"
)
TELEGRAM_GREEN = REPO / "openspec/changes/telegram-lead-form/apply-green/SLICE-001/GREEN.json"
GREEN_DOC = REPO / ".cursor" / "commands" / "opsx-apply-green.md"
IMPLEMENT_DOC = REPO / ".cursor" / "commands" / "opsx-apply-implement.md"
S_EV1 = REPO / ".cursor" / "pipeline" / "evidence-integrity" / "evidence_integrity.py"
CONTRACT = "EVIDENCE_INTEGRITY_ADOPTION_V0"
FORBIDDEN_STDOUT = ("VERIFIED_AT_TIER_T2", "apply_complete", "completion_class")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("adoption_authority", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


AA = _load()


def _manifest() -> dict[str, object]:
    return {
        "slice_id": "SLICE-001",
        "production_manifest": [{"path": "src/a.py", "sha256": "ab"}],
    }


def _checks() -> list[dict[str, str]]:
    return [
        {"id": f"AC{index}", "status": "PASS", "evidence_ref": f"owner-message:ac{index}"}
        for index in range(1, 7)
    ]


def _audit(**overrides: object) -> dict[str, object]:
    digest = AA.candidate_digest(_manifest())
    body: dict[str, object] = {
        "schema": "opsx.adoption_audit",
        "schema_version": 1,
        "change_id": "demo",
        "slice_id": "SLICE-001",
        "slice_class": "behavioral",
        "candidate_digest": digest,
        "manifest_sha256": digest,
        "auditor": {"agent_id": "auditor-1", "model": "inherit", "scope": "READ_ONLY"},
        "writer_ids": {"red": "red-1", "implement": "impl-1", "green": "green-1"},
        "red_status": "ALREADY_SATISFIED",
        "checks": _checks(),
        "behavior_assertions": [
            {
                "scenario_id": "missing-id",
                "assertion": "client receives not-found when the id is absent",
            }
        ],
        "causal_witness": {"kind": "PRE_IMPL_REPLAY", "evidence_ref": "owner-message:witness"},
        "status": "AUDIT_PASS",
    }
    body.update(overrides)
    return body


def _authority(audit: dict[str, object], **overrides: object) -> dict[str, object]:
    digest = str(audit["candidate_digest"])
    receipt = AA.audit_receipt_sha256(audit)
    ref = "owner-message:s-ev2-fixture"
    body: dict[str, object] = {
        "schema": "opsx.current_byte_adoption_authority",
        "schema_version": 2,
        "authorization_class": "CURRENT_BYTE_OWNER_ADOPTION",
        "candidate_digest": digest,
        "audit_receipt_sha256": receipt,
        "owner_attestation_ref": ref,
        "bindings": {
            "candidate_digest": digest,
            "audit_receipt_sha256": receipt,
            "owner_attestation_ref": ref,
        },
        "content_hash": "present-not-sufficient",
    }
    body.update(overrides)
    return body


def _implementation(audit: dict[str, object], authority: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "status": "IMPLEMENTATION_ADOPTED",
        "branch": "ADOPTION",
        "adoption_contract": CONTRACT,
        "product_files_modified": False,
        "tests_modified": False,
        "tests_executed": False,
        "chat_history_required": False,
        "next_stage": "APPLY_GREEN",
        "red_identity": {"status": "ALREADY_SATISFIED"},
        "current_byte_adoption_authority": {
            "schema_version": 2,
            "candidate_digest": audit["candidate_digest"],
            "audit_receipt_sha256": authority["audit_receipt_sha256"],
            "owner_attestation_ref": authority["owner_attestation_ref"],
        },
    }


def _codes(report: dict[str, object]) -> list[str]:
    findings = report["findings"]
    assert isinstance(findings, list)
    return [str(item["code"]) for item in findings]


def _assert_ceiling(report: dict[str, object]) -> None:
    raw = json.dumps(report, sort_keys=True)
    for token in FORBIDDEN_STDOUT:
        assert token not in raw
    assert report["owner_authentication"] == "UNPROVEN"
    assert report["live_authority"] == "NO"
    assert report["writes"] == []
    assert report["platform_attestation"] == "UNPROVEN"


def _node_json(script: str) -> dict[str, object]:
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_ei_n02_adopted_without_audit_is_missing() -> None:
    report = AA.check_adoption(
        {
            "implementation": {
                "status": "IMPLEMENTATION_ADOPTED",
                "adoption_contract": CONTRACT,
                "current_byte_adoption_authority": {
                    "schema_version": 2,
                    "candidate_digest": "a" * 64,
                    "audit_receipt_sha256": "b" * 64,
                },
            }
        }
    )
    assert "ADOPTION_AUDIT_MISSING" in _codes(report)
    assert report["authority_emitted"] is False
    assert report["implementation_adopted_authorized"] is False


def test_ei_n03_auditor_matches_writer() -> None:
    audit = _audit(writer_ids={"red": "auditor-1", "implement": "impl-1", "green": "green-1"})
    report = AA.check_adoption({"audit": audit, "manifest": _manifest()})
    assert report["effective_audit_status"] == "AUDIT_NOT_INDEPENDENT"
    assert "AUDIT_NOT_INDEPENDENT" in _codes(report)
    assert report["authority_emitted"] is False


def test_ei_n04_digest_stale_after_manifest_change() -> None:
    changed = _manifest()
    changed["production_manifest"] = [{"path": "src/a.py", "sha256": "ff"}]
    report = AA.check_adoption({"audit": _audit(), "manifest": changed})
    assert report["effective_audit_status"] == "AUDIT_STALE"
    assert "AUDIT_STALE" in _codes(report)
    assert report["authority_emitted"] is False


def test_ei_n05_token_or_unbound_v2_is_invalid() -> None:
    token = AA.check_adoption(
        {"authority": {"authorization_class": "AUTHORIZE_CURRENT_BYTE_ADOPTION"}}
    )
    unbound = AA.check_adoption(
        {
            "authority": {
                "schema": "opsx.current_byte_adoption_authority",
                "schema_version": 2,
                "candidate_digest": "a" * 64,
            }
        }
    )
    assert "ADOPTION_AUTHORITY_INVALID" in _codes(token)
    assert "ADOPTION_AUTHORITY_INVALID" in _codes(unbound)
    assert token["authority_emitted"] is False
    assert unbound["authority_emitted"] is False


def test_ei_n07_content_hash_does_not_excuse_wrong_audit_sha() -> None:
    audit = _audit()
    authority = _authority(audit)
    authority["audit_receipt_sha256"] = "0" * 64
    bindings = authority["bindings"]
    assert isinstance(bindings, dict)
    bindings["audit_receipt_sha256"] = "0" * 64
    authority["content_hash"] = "self-consistent-content-hash"
    report = AA.check_adoption({"audit": audit, "authority": authority, "manifest": _manifest()})
    assert "ADOPTION_AUTHORITY_INVALID" in _codes(report)
    assert report["authority_emitted"] is False


def test_ei_n13_behavioral_none_or_vacuous_is_not_pass() -> None:
    none_witness = _audit(causal_witness={"kind": "NONE"})
    vacuous = _audit(
        behavior_assertions=[
            {"scenario_id": "vacuous", "assertion": "assert 'NotImplementedError' in getsource(fn)"}
        ]
    )
    none_report = AA.check_adoption({"audit": none_witness, "manifest": _manifest()})
    vacuous_report = AA.check_adoption({"audit": vacuous, "manifest": _manifest()})
    assert none_report["effective_audit_status"] != "AUDIT_PASS"
    assert vacuous_report["effective_audit_status"] == "AUDIT_FAIL"
    assert none_report["authority_emitted"] is False
    assert vacuous_report["authority_emitted"] is False


def test_ei_n16_passing_checks_without_behavior_assertion() -> None:
    audit = _audit(behavior_assertions=[])
    report = AA.check_adoption({"audit": audit, "manifest": _manifest()})
    assert report["effective_audit_status"] == "AUDIT_FAIL"
    assert "AUDIT_FAIL" in _codes(report)
    assert report["implementation_adopted_authorized"] is False


def test_ei_n17_inconclusive_emits_no_authority() -> None:
    audit = _audit(status="AUDIT_INCONCLUSIVE")
    authority = _authority(audit)
    report = AA.check_adoption(
        {"audit": audit, "authority": authority, "manifest": _manifest()}
    )
    assert report["effective_audit_status"] == "AUDIT_INCONCLUSIVE"
    assert report["authority_emitted"] is False
    assert report["authority_permitted"] is False


def test_ei_n24_green_doc_lists_adoption_pair() -> None:
    text = GREEN_DOC.read_text(encoding="utf-8")
    section = text.split("## Slice selection", 1)[1].split("## Frozen RED integrity", 1)[0]
    assert "ALREADY_SATISFIED" in section
    assert "IMPLEMENTATION_ADOPTED" in section
    assert "RED_PROVEN" in section
    assert "IMPLEMENTATION_WRITTEN" in section


def test_ei_p02_behavioral_chain_shares_digest_and_hides_terminal_verdict() -> None:
    audit = _audit()
    authority = _authority(audit)
    implementation = _implementation(audit, authority)
    report = AA.check_adoption(
        {
            "audit": audit,
            "authority": authority,
            "implementation": implementation,
            "manifest": _manifest(),
            "red_status": "ALREADY_SATISFIED",
        }
    )
    _assert_ceiling(report)
    assert report["effective_audit_status"] == "AUDIT_PASS"
    assert report["implementation_adopted_authorized"] is True
    assert report["authority_emitted"] is True
    assert report["red_status"] == "ALREADY_SATISFIED"
    assert report["causal_witness_kind"] == "PRE_IMPL_REPLAY"
    assert report["candidate_digest"] == audit["candidate_digest"]
    assert "ATTESTATION_CONTENT_UNVERIFIED" in _codes(report)
    assert "OWNER_AUTHENTICATION_UNPROVEN" in _codes(report)
    assert "AUTHENTICATED" not in json.dumps(report)


def test_ei_p03_structural_none_passes_behavioral_none_does_not() -> None:
    structural = _audit(
        slice_class="structural_or_config",
        causal_witness={"kind": "NONE"},
        checks=[],
        behavior_assertions=[],
    )
    behavioral = _audit(causal_witness={"kind": "NONE"})
    structural_report = AA.check_adoption({"audit": structural, "manifest": _manifest()})
    behavioral_report = AA.check_adoption({"audit": behavioral, "manifest": _manifest()})
    assert structural_report["effective_audit_status"] == "AUDIT_PASS"
    assert behavioral_report["effective_audit_status"] != "AUDIT_PASS"


def test_ei_p09_pre_impl_replay_keeps_red_already_satisfied() -> None:
    audit = _audit()
    report = AA.check_adoption(
        {
            "audit": audit,
            "manifest": _manifest(),
            "owner_attestation_ref": "owner-message:s-ev2-fixture",
            "red_status": "ALREADY_SATISFIED",
        }
    )
    assert report["causal_witness_kind"] == "PRE_IMPL_REPLAY"
    assert report["red_status"] == "ALREADY_SATISFIED"
    assert report["authority_permitted"] is True
    assert "RED_PROVEN" not in json.dumps(report["findings"])


def test_m1_missing_slice_class_is_not_pass() -> None:
    audit = _audit()
    del audit["slice_class"]
    report = AA.check_adoption({"audit": audit, "manifest": _manifest()})
    assert report["effective_audit_status"] != "AUDIT_PASS"
    assert report["authority_emitted"] is False


def test_m2_bad_prefix_is_not_owner_held() -> None:
    audit = _audit()
    authority = _authority(audit, owner_attestation_ref="/tmp/self-written-note")
    bindings = authority["bindings"]
    assert isinstance(bindings, dict)
    bindings["owner_attestation_ref"] = "/tmp/self-written-note"
    report = AA.check_adoption({"audit": audit, "authority": authority, "manifest": _manifest()})
    assert "ATTESTATION_REF_NOT_OWNER_HELD" in _codes(report)
    assert report["owner_authentication"] == "UNPROVEN"
    assert report["authority_emitted"] is False


def test_m3_prefix_match_stays_unproven() -> None:
    audit = _audit()
    authority = _authority(audit)
    report = AA.check_adoption({"audit": audit, "authority": authority, "manifest": _manifest()})
    assert report["owner_authentication"] == "UNPROVEN"
    assert "OWNER_AUTHENTICATION_UNPROVEN" in _codes(report)
    assert report["authority_emitted"] is True


def test_m4_delegation_grant_rejected() -> None:
    audit = _audit()
    authority = _authority(audit)
    authority["delegation_grant"] = {"grant_id": "scoped"}
    report = AA.check_adoption({"audit": audit, "authority": authority, "manifest": _manifest()})
    assert "ADOPTION_AUTHORITY_INVALID" in _codes(report)
    assert report["implementation_adopted_authorized"] is False
    assert report["authority_emitted"] is False


def test_m5_missing_writer_id_is_inconclusive() -> None:
    audit = _audit(writer_ids={"red": "red-1", "implement": "impl-1"})
    report = AA.check_adoption({"audit": audit, "manifest": _manifest()})
    assert report["effective_audit_status"] == "AUDIT_INCONCLUSIVE"
    assert report["authority_emitted"] is False


def test_m6_audit_fail_emits_no_authority() -> None:
    audit = _audit(status="AUDIT_FAIL")
    authority = _authority(audit)
    report = AA.check_adoption({"audit": audit, "authority": authority, "manifest": _manifest()})
    assert report["effective_audit_status"] == "AUDIT_FAIL"
    assert report["authority_emitted"] is False


def test_m7_refresh_guard_rejects_fake_normal_pair() -> None:
    payload = _node_json(
        """
        import { guardRefreshNormalPredecessors } from "./opsx_tools/opsx-green-refresh-slice.mjs";
        const bad = guardRefreshNormalPredecessors({
          red: { status: "ALREADY_SATISFIED" },
          implement: { status: "BLOCKED" },
        });
        const good = guardRefreshNormalPredecessors({
          red: { status: "RED_PROVEN" },
          implement: { status: "IMPLEMENTATION_WRITTEN" },
        });
        console.log(JSON.stringify({ bad, good }));
        """
    )
    bad = payload["bad"]
    good = payload["good"]
    assert isinstance(bad, dict) and isinstance(good, dict)
    assert bad["ok"] is False
    assert bad["code"] == "REFRESH_NORMAL_PREDECESSOR_FORBIDDEN"
    assert good["ok"] is True


def test_m8_token_only_does_not_adopt() -> None:
    payload = _node_json(
        """
        import { evaluateAdoptionAuthorization, AUTHORIZE_CURRENT_BYTE_ADOPTION } from "./opsx_tools/opsx-apply-implement.mjs";
        const result = evaluateAdoptionAuthorization({
          ownerToken: AUTHORIZE_CURRENT_BYTE_ADOPTION,
          check: null,
        });
        console.log(JSON.stringify(result));
        """
    )
    assert payload["ok"] is False
    assert payload["code"] == "ADOPTION_AUDIT_MISSING"
    assert payload.get("status") != "IMPLEMENTATION_ADOPTED"


def test_m9_legacy_v1_parser_non_authorizing_transition_gate_fails(tmp_path: Path) -> None:
    artifact = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_executed": False,
        "tests_modified": False,
        "status": "IMPLEMENTATION_ADOPTED",
        "branch": "ADOPTION",
        "next_stage": "APPLY_GREEN",
        "product_files_modified": False,
        "current_byte_adoption_authority": {"candidate_digest": "abc123"},
        "red_identity": {"status": "ALREADY_SATISFIED"},
    }
    path = tmp_path / "IMPLEMENTATION.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(IMPLEMENT), str(path)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert cli.get("transition_authorized") is False
    assert any(
        item["code"] in {"ADOPTION_AUTHORITY_LEGACY", "ADOPTION_AUDIT_MISSING"}
        for item in cli["errors"]
    )
    legacy = AA.check_adoption(
        {
            "authority": {
                "schema": "opsx.current_byte_adoption_authority",
                "schema_version": 1,
                "authorization_class": "CURRENT_BYTE_OWNER_ADOPTION",
                "candidate_digest": "abc123",
                "content_hash": "legacy",
            }
        }
    )
    assert "ADOPTION_AUTHORITY_LEGACY" in _codes(legacy)
    assert legacy["authority_emitted"] is False
    assert legacy["implementation_adopted_authorized"] is False


def test_m10_checker_stdout_has_no_terminal_verdict(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.json"
    audit = _audit()
    bundle.write_text(
        json.dumps({"audit": audit, "manifest": _manifest(), "red_status": "ALREADY_SATISFIED"}),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["python3", str(MODULE_PATH), "--input", str(bundle)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    for token in FORBIDDEN_STDOUT:
        assert token not in completed.stdout
    report = json.loads(completed.stdout)
    assert report["owner_authentication"] == "UNPROVEN"
    assert report["writes"] == []


def test_v2_cli_missing_audit_fails_closed(tmp_path: Path) -> None:
    artifact = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_executed": False,
        "tests_modified": False,
        "status": "IMPLEMENTATION_ADOPTED",
        "branch": "ADOPTION",
        "adoption_contract": CONTRACT,
        "next_stage": "APPLY_GREEN",
        "product_files_modified": False,
        "current_byte_adoption_authority": {
            "schema_version": 2,
            "candidate_digest": "a" * 64,
            "audit_receipt_sha256": "b" * 64,
            "owner_attestation_ref": "owner-message:s-ev2-fixture",
        },
        "red_identity": {"status": "ALREADY_SATISFIED"},
    }
    path = tmp_path / "IMPLEMENTATION.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(IMPLEMENT), str(path)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert any(item["code"] == "ADOPTION_AUDIT_MISSING" for item in cli["errors"])


def test_adopt_route_emits_only_after_checker_pass(tmp_path: Path) -> None:
    audit = _audit()
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_text(
        json.dumps(
            {
                "audit": audit,
                "manifest": _manifest(),
                "owner_attestation_ref": "owner-message:s-ev2-fixture",
                "change_id": "demo",
                "slice_id": "SLICE-001",
                "red_status": "ALREADY_SATISFIED",
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(IMPLEMENT), "--adopt-bundle", str(bundle_path)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    for token in FORBIDDEN_STDOUT:
        assert token not in completed.stdout
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["authority"]["schema_version"] == 2
    assert result["implementation"]["status"] == "IMPLEMENTATION_ADOPTED"
    assert result["implementation"]["red_identity"]["status"] == "ALREADY_SATISFIED"
    assert result["implementation"]["product_files_modified"] is False
    assert result["implementation"]["tests_modified"] is False
    assert result["check"]["owner_authentication"] == "UNPROVEN"
    assert result["authority"]["candidate_digest"] == audit["candidate_digest"]
    assert (
        result["implementation"]["current_byte_adoption_authority"]["candidate_digest"]
        == result["authority"]["candidate_digest"]
    )
    assert result["authority"]["audit_receipt_sha256"] == result["check"]["audit_receipt_sha256"]
    assert result["authority"]["bindings"]["candidate_digest"] == result["authority"]["candidate_digest"]

    rejected = tmp_path / "rejected.json"
    rejected_audit = _audit(causal_witness={"kind": "NONE"})
    rejected.write_text(
        json.dumps(
            {
                "audit": rejected_audit,
                "manifest": _manifest(),
                "owner_attestation_ref": "owner-message:s-ev2-fixture",
                "change_id": "demo",
                "slice_id": "SLICE-001",
            }
        ),
        encoding="utf-8",
    )
    failed = subprocess.run(
        ["node", str(IMPLEMENT), "--adopt-bundle", str(rejected)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 1
    failed_body = json.loads(failed.stdout)
    assert failed_body["authority"] is None
    assert failed_body["implementation"] is None


def test_node_validator_rejects_self_hash_with_wrong_audit_sha() -> None:
    payload = _node_json(
        """
        import { createHash } from "node:crypto";
        import { buildCurrentByteAdoptionAuthority, validateCurrentByteAdoptionAuthority } from "./opsx_tools/opsx-apply-implement.mjs";
        const body = buildCurrentByteAdoptionAuthority({
          changeId: "demo",
          sliceId: "SLICE-001",
          candidateDigest: "c".repeat(64),
          auditReceiptSha256: "a".repeat(64),
          ownerAttestationRef: "owner-message:fixture",
        });
        body.audit_receipt_sha256 = "b".repeat(64);
        body.bindings.audit_receipt_sha256 = "b".repeat(64);
        delete body.content_hash;
        body.content_hash = createHash("sha256")
          .update(JSON.stringify({ ...body, content_hash: undefined }))
          .digest("hex");
        const valid = validateCurrentByteAdoptionAuthority(body, { auditReceiptSha256: "a".repeat(64) });
        console.log(JSON.stringify(valid));
        """
    )
    assert payload["ok"] is False
    assert payload["code"] == "ADOPTION_AUTHORITY_INVALID"


def test_s_ev1_module_stays_detect_only() -> None:
    source = S_EV1.read_text(encoding="utf-8")
    assert 'gating", "DETECT_ONLY"' in source or '"DETECT_ONLY"' in source
    assert "ADOPTION_AUDIT_MISSING" not in source
    assert "adoption_authority" not in source


def test_implement_doc_describes_adoption_branch() -> None:
    text = IMPLEMENT_DOC.read_text(encoding="utf-8")
    assert "IMPLEMENTATION_ADOPTED" in text
    assert "opsx.adoption_audit" in text
    assert "no product or test writes" in text.lower() or "no product code or tests" in text.lower()


def test_telegram_adoption_implement_default_validate_fails_closed() -> None:
    completed = subprocess.run(
        ["node", str(IMPLEMENT), str(TELEGRAM_IMPLEMENTATION)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert cli.get("transition_authorized") is False


def test_historical_legacy_implement_cli_readable_at_boundary() -> None:
    """READ_LEGACY снят: legacy v1 без audit остаётся FAIL."""
    completed = subprocess.run(
        ["node", str(IMPLEMENT), str(TELEGRAM_IMPLEMENTATION)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert cli.get("transition_authorized") is False


def test_telegram_adoption_green_default_validate_fails_closed() -> None:
    completed = subprocess.run(
        ["node", str(GREEN), str(TELEGRAM_GREEN)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert cli.get("green_adoption_mode") == "VALIDATE_GREEN_TRANSITION"
    assert cli.get("transition_authorized") is False


def test_historical_legacy_adoption_green_cli_readable_at_boundary() -> None:
    """READ_LEGACY снят: historical ADOPTION GREEN без authority FAIL."""
    completed = subprocess.run(
        ["node", str(GREEN), str(TELEGRAM_GREEN)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert cli.get("green_adoption_mode") == "VALIDATE_GREEN_TRANSITION"
    assert cli.get("transition_authorized") is False


def test_fresh_v1_implement_cli_fails_closed(tmp_path: Path) -> None:
    artifact = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_executed": False,
        "tests_modified": False,
        "status": "IMPLEMENTATION_ADOPTED",
        "branch": "ADOPTION",
        "next_stage": "APPLY_GREEN",
        "product_files_modified": False,
        "current_byte_adoption_authority": {"candidate_digest": "fresh-v1-not-historical"},
        "red_identity": {"status": "ALREADY_SATISFIED"},
    }
    path = tmp_path / "FRESH-IMPLEMENTATION.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(IMPLEMENT), str(path)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert cli.get("transition_authorized") is False


def test_fresh_v1_green_cli_fails_closed(tmp_path: Path) -> None:
    artifact = {
        "schema": "opsx.apply_green",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_modified": False,
        "status": "GREEN_PROVEN",
        "predecessor_branch": "ADOPTION",
        "red_identity": {"status": "ALREADY_SATISFIED"},
        "implementation_identity": {"status": "IMPLEMENTATION_ADOPTED"},
        "scaffold_identity": {"status": "SCAFFOLD_READY"},
        "red_final_status": "PASS",
        "red_executions": [{"exit_code": 0}],
        "whole_change_verified": False,
    }
    path = tmp_path / "FRESH-GREEN.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(GREEN), str(path)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert any(
        item["code"] in {"ADOPTION_AUDIT_MISSING", "ADOPTION_AUTHORITY_LEGACY"}
        for item in cli["errors"]
    )


def test_select_next_greenable_slice_rejects_unauthorized_adoption() -> None:
    payload = _node_json(
        """
        import { selectNextGreenableSlice } from "./opsx_tools/opsx-apply-pipeline-identity.mjs";
        const design = {
          status: "APPLY_DESIGN_READY",
          slice_order: { kind: "dependency_graph", nodes: ["SLICE-NEW"], edges: [] },
          slices: [{
            slice_id: "SLICE-NEW",
            source_task_ids: ["T1"],
            dependencies_on_prior_slices: [],
          }],
        };
        const red = {
          "SLICE-NEW": {
            status: "ALREADY_SATISFIED",
            schema_version: 2,
            evidence_contract: "per_command_v1",
            tests_frozen: true,
            test_commands: ["echo ok"],
            command_executions: [{
              command: "echo ok",
              red_qualified: true,
              final_classification: "INTENDED_BEHAVIOR_MISSING",
              attempts: [{ exit_code: 0 }],
            }],
          },
        };
        const implement = {
          "SLICE-NEW": {
            status: "IMPLEMENTATION_ADOPTED",
            branch: "ADOPTION",
            next_stage: "APPLY_GREEN",
            current_byte_adoption_authority: { candidate_digest: "fresh-v1-not-historical" },
            red_identity: { status: "ALREADY_SATISFIED" },
          },
        };
        const scaffold = { "SLICE-NEW": { status: "SCAFFOLD_READY" } };
        const pick = selectNextGreenableSlice({
          design,
          redBySlice: red,
          implementBySlice: implement,
          scaffoldBySlice: scaffold,
          greenBySlice: {},
        });
        console.log(JSON.stringify(pick));
        """
    )
    assert payload["ok"] is False
    assert payload["code"] == "NO_ELIGIBLE_SLICE"


def test_residual_alias_implement_abs_authority_fails_closed(tmp_path: Path) -> None:
    """Independent audit exploit: /tmp v1 authority + absolute machine_path must not PASS."""
    authority = tmp_path / "FRESH-AUTHORITY.json"
    authority.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authorization_class": "CURRENT_BYTE_OWNER_ADOPTION",
                "candidate_digest": "fresh-minted-v1-digest",
            }
        ),
        encoding="utf-8",
    )
    artifact = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_executed": False,
        "tests_modified": False,
        "status": "IMPLEMENTATION_ADOPTED",
        "branch": "ADOPTION",
        "change_id": "fresh-not-telegram",
        "slice_id": "SLICE-FRESH",
        "next_stage": "APPLY_GREEN",
        "product_files_modified": False,
        "current_byte_adoption_authority": {
            "machine_path": str(authority),
            "candidate_digest": "fresh-minted-v1-digest",
        },
        "red_identity": {"status": "ALREADY_SATISFIED"},
    }
    path = tmp_path / "ALIAS-IMPLEMENT-ABS-AUTHORITY.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(IMPLEMENT), str(path)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert cli.get("transition_authorized") is False
    assert any(
        item["code"] in {"ADOPTION_AUTHORITY_LEGACY", "ADOPTION_AUDIT_MISSING"}
        for item in cli["errors"]
    )


def test_residual_alias_green_abs_implement_fails_closed(tmp_path: Path) -> None:
    """Independent audit exploit: ADOPTION GREEN linking /tmp alias implement must FAIL."""
    authority = tmp_path / "FRESH-AUTHORITY.json"
    authority.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authorization_class": "CURRENT_BYTE_OWNER_ADOPTION",
                "candidate_digest": "fresh-minted-v1-digest",
            }
        ),
        encoding="utf-8",
    )
    implement = tmp_path / "ALIAS-IMPLEMENT-ABS-AUTHORITY.json"
    implement.write_text(
        json.dumps(
            {
                "schema": "opsx.apply_implement",
                "schema_version": 1,
                "chat_history_required": False,
                "tests_executed": False,
                "tests_modified": False,
                "status": "IMPLEMENTATION_ADOPTED",
                "branch": "ADOPTION",
                "change_id": "fresh-not-telegram",
                "slice_id": "SLICE-FRESH",
                "next_stage": "APPLY_GREEN",
                "product_files_modified": False,
                "current_byte_adoption_authority": {
                    "machine_path": str(authority),
                    "candidate_digest": "fresh-minted-v1-digest",
                },
                "red_identity": {"status": "ALREADY_SATISFIED"},
            }
        ),
        encoding="utf-8",
    )
    green = {
        "schema": "opsx.apply_green",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_modified": False,
        "status": "GREEN_PROVEN",
        "predecessor_branch": "ADOPTION",
        "change_id": "fresh-not-telegram",
        "slice_id": "SLICE-FRESH",
        "red_identity": {"status": "ALREADY_SATISFIED"},
        "implementation_identity": {
            "machine_path": str(implement),
            "status": "IMPLEMENTATION_ADOPTED",
        },
        "scaffold_identity": {"status": "SCAFFOLD_READY"},
        "red_final_status": "PASS",
        "red_executions": [{"exit_code": 0}],
        "whole_change_verified": False,
    }
    path = tmp_path / "ALIAS-GREEN-ABS-IMPLEMENT.json"
    path.write_text(json.dumps(green), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(GREEN), str(path)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert any(
        item.get("transition_gate") and item["code"] in {"ADOPTION_AUTHORITY_LEGACY", "ADOPTION_AUDIT_MISSING"}
        for item in cli["errors"]
    )


def test_self_canonical_fresh_implement_at_boundary_fails_closed(tmp_path: Path) -> None:
    """Third-audit exploit C: self-declared canonical path must not skip transition gate."""
    sandbox = tmp_path / "sandbox-repo"
    change_id = "fresh-not-telegram"
    slice_id = "SLICE-FRESH"
    impl_dir = sandbox / "openspec/changes" / change_id / "apply-implement" / slice_id
    impl_dir.mkdir(parents=True)
    authority_rel = (
        f"openspec/changes/{change_id}/apply-implement/{slice_id}/AUTHORITY.json"
    )
    authority = impl_dir / "AUTHORITY.json"
    authority.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authorization_class": "CURRENT_BYTE_OWNER_ADOPTION",
                "candidate_digest": "fresh-minted-v1-digest",
            }
        ),
        encoding="utf-8",
    )
    artifact = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_executed": False,
        "tests_modified": False,
        "status": "IMPLEMENTATION_ADOPTED",
        "branch": "ADOPTION",
        "change_id": change_id,
        "slice_id": slice_id,
        "next_stage": "APPLY_GREEN",
        "product_files_modified": False,
        "current_byte_adoption_authority": {
            "machine_path": authority_rel,
            "candidate_digest": "fresh-minted-v1-digest",
        },
        "red_identity": {"status": "ALREADY_SATISFIED"},
    }
    impl_path = impl_dir / "IMPLEMENTATION.json"
    impl_path.write_text(json.dumps(artifact), encoding="utf-8")
    completed = subprocess.run(
        [
            "node",
            str(IMPLEMENT),
            "--repo",
            str(sandbox),
            str(impl_path),
        ],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert cli.get("transition_authorized") is False


def test_committed_head_fresh_green_pair_fails_transition_validate(tmp_path: Path) -> None:
    """Fourth-audit exploit: committed HEAD blob match must not bypass VALIDATE_GREEN_TRANSITION."""
    sandbox = tmp_path / "git-sandbox"
    change_id = "fresh-not-telegram"
    slice_id = "SLICE-FRESH"
    impl_dir = sandbox / "openspec/changes" / change_id / "apply-implement" / slice_id
    green_dir = sandbox / "openspec/changes" / change_id / "apply-green" / slice_id
    impl_dir.mkdir(parents=True)
    green_dir.mkdir(parents=True)
    authority_rel = (
        f"openspec/changes/{change_id}/apply-implement/{slice_id}/AUTHORITY.json"
    )
    authority = impl_dir / "AUTHORITY.json"
    authority.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authorization_class": "CURRENT_BYTE_OWNER_ADOPTION",
                "candidate_digest": "fresh-minted-v1-digest",
            }
        ),
        encoding="utf-8",
    )
    implement_rel = (
        f"openspec/changes/{change_id}/apply-implement/{slice_id}/IMPLEMENTATION.json"
    )
    implement_body = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_executed": False,
        "tests_modified": False,
        "status": "IMPLEMENTATION_ADOPTED",
        "branch": "ADOPTION",
        "change_id": change_id,
        "slice_id": slice_id,
        "next_stage": "APPLY_GREEN",
        "product_files_modified": False,
        "current_byte_adoption_authority": {
            "machine_path": authority_rel,
            "candidate_digest": "fresh-minted-v1-digest",
        },
        "red_identity": {"status": "ALREADY_SATISFIED"},
    }
    impl_path = impl_dir / "IMPLEMENTATION.json"
    impl_path.write_text(json.dumps(implement_body), encoding="utf-8")
    green_body = {
        "schema": "opsx.apply_green",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_modified": False,
        "status": "GREEN_PROVEN",
        "predecessor_branch": "ADOPTION",
        "change_id": change_id,
        "slice_id": slice_id,
        "red_identity": {"status": "ALREADY_SATISFIED"},
        "implementation_identity": {
            "machine_path": implement_rel,
            "status": "IMPLEMENTATION_ADOPTED",
        },
        "scaffold_identity": {"status": "SCAFFOLD_READY"},
        "red_final_status": "PASS",
        "red_executions": [{"exit_code": 0}],
        "whole_change_verified": False,
    }
    green_path = green_dir / "GREEN.json"
    green_path.write_text(json.dumps(green_body), encoding="utf-8")
    subprocess.run(["git", "init"], cwd=sandbox, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=sandbox, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-m", "mint"],
        cwd=sandbox,
        check=True,
        capture_output=True,
    )
    green_completed = subprocess.run(
        ["node", str(GREEN), "--repo", str(sandbox), str(green_path)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert green_completed.returncode == 1
    green_cli = json.loads(green_completed.stdout)
    assert green_cli["status"] == "FAIL"
    assert green_cli.get("green_adoption_mode") == "VALIDATE_GREEN_TRANSITION"
    assert green_cli.get("transition_authorized") is False
    assert any(item.get("transition_gate") for item in green_cli["errors"])


def test_self_canonical_fresh_green_pair_at_boundary_fails_closed(tmp_path: Path) -> None:
    """Third-audit exploit C/G: ADOPTION GREEN on fresh canonical pair must exit 1."""
    sandbox = tmp_path / "sandbox-repo"
    change_id = "fresh-not-telegram"
    slice_id = "SLICE-FRESH"
    impl_dir = sandbox / "openspec/changes" / change_id / "apply-implement" / slice_id
    green_dir = sandbox / "openspec/changes" / change_id / "apply-green" / slice_id
    impl_dir.mkdir(parents=True)
    green_dir.mkdir(parents=True)
    authority_rel = (
        f"openspec/changes/{change_id}/apply-implement/{slice_id}/AUTHORITY.json"
    )
    authority = impl_dir / "AUTHORITY.json"
    authority.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "authorization_class": "CURRENT_BYTE_OWNER_ADOPTION",
                "candidate_digest": "fresh-minted-v1-digest",
            }
        ),
        encoding="utf-8",
    )
    implement_rel = (
        f"openspec/changes/{change_id}/apply-implement/{slice_id}/IMPLEMENTATION.json"
    )
    implement_body = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_executed": False,
        "tests_modified": False,
        "status": "IMPLEMENTATION_ADOPTED",
        "branch": "ADOPTION",
        "change_id": change_id,
        "slice_id": slice_id,
        "next_stage": "APPLY_GREEN",
        "product_files_modified": False,
        "current_byte_adoption_authority": {
            "machine_path": authority_rel,
            "candidate_digest": "fresh-minted-v1-digest",
        },
        "red_identity": {"status": "ALREADY_SATISFIED"},
    }
    impl_path = impl_dir / "IMPLEMENTATION.json"
    impl_path.write_text(json.dumps(implement_body), encoding="utf-8")
    green_body = {
        "schema": "opsx.apply_green",
        "schema_version": 1,
        "chat_history_required": False,
        "tests_modified": False,
        "status": "GREEN_PROVEN",
        "predecessor_branch": "ADOPTION",
        "change_id": change_id,
        "slice_id": slice_id,
        "red_identity": {"status": "ALREADY_SATISFIED"},
        "implementation_identity": {
            "machine_path": implement_rel,
            "status": "IMPLEMENTATION_ADOPTED",
        },
        "scaffold_identity": {"status": "SCAFFOLD_READY"},
        "red_final_status": "PASS",
        "red_executions": [{"exit_code": 0}],
        "whole_change_verified": False,
    }
    green_path = green_dir / "GREEN.json"
    green_path.write_text(json.dumps(green_body), encoding="utf-8")
    completed = subprocess.run(
        [
            "node",
            str(GREEN),
            "--repo",
            str(sandbox),
            str(green_path),
        ],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert any(item.get("transition_gate") for item in cli["errors"])


def test_mutated_telegram_slice002_read_legacy_fails_closed(tmp_path: Path) -> None:
    """Mutated SLICE-002 adoption artifacts fail transition validation."""
    sandbox = tmp_path / "mutated-slice002"
    slice_dir = sandbox / "openspec/changes/telegram-lead-form"
    impl_dir = slice_dir / "apply-implement" / "SLICE-002"
    green_dir = slice_dir / "apply-green" / "SLICE-002"
    impl_dir.mkdir(parents=True)
    green_dir.mkdir(parents=True)
    for src, dst in (
        (
            REPO
            / "openspec/changes/telegram-lead-form/apply-implement/SLICE-002/IMPLEMENTATION.json",
            impl_dir / "IMPLEMENTATION.json",
        ),
        (
            REPO
            / "openspec/changes/telegram-lead-form/apply-implement/SLICE-002/CURRENT-BYTE-ADOPTION-AUTHORITY-a28b8f6443b28380b8f54bbdc905acd7ba719aa4823c6dd1384607ca2d7b795a.json",
            impl_dir
            / "CURRENT-BYTE-ADOPTION-AUTHORITY-a28b8f6443b28380b8f54bbdc905acd7ba719aa4823c6dd1384607ca2d7b795a.json",
        ),
        (
            REPO / "openspec/changes/telegram-lead-form/apply-green/SLICE-002/GREEN.json",
            green_dir / "GREEN.json",
        ),
    ):
        dst.write_bytes(src.read_bytes())
    authority_path = (
        impl_dir
        / "CURRENT-BYTE-ADOPTION-AUTHORITY-a28b8f6443b28380b8f54bbdc905acd7ba719aa4823c6dd1384607ca2d7b795a.json"
    )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority["candidate_digest"] = "mutated-not-product-blob"
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    impl_body = json.loads((impl_dir / "IMPLEMENTATION.json").read_text(encoding="utf-8"))
    impl_body["current_byte_adoption_authority"]["candidate_digest"] = "mutated-not-product-blob"
    (impl_dir / "IMPLEMENTATION.json").write_text(json.dumps(impl_body), encoding="utf-8")
    subprocess.run(["git", "init"], cwd=sandbox, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=sandbox, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-m", "mutate"],
        cwd=sandbox,
        check=True,
        capture_output=True,
    )
    green_path = green_dir / "GREEN.json"
    for cli_tool, extra in ((IMPLEMENT, impl_dir / "IMPLEMENTATION.json"), (GREEN, green_path)):
        completed = subprocess.run(
            ["node", str(cli_tool), "--repo", str(sandbox), str(extra)],
            cwd=REPO,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 1, completed.stdout + completed.stderr
        payload = json.loads(completed.stdout)
        assert payload["status"] == "FAIL"
        assert payload.get("transition_authorized") is False


def test_residual_historical_pointer_green_in_tmp_fails_closed(tmp_path: Path) -> None:
    """Telegram GREEN body at /tmp with fresh change_id must not inherit historical exemption."""
    base = json.loads(TELEGRAM_GREEN.read_text(encoding="utf-8"))
    base["change_id"] = "fresh-not-telegram"
    base["slice_id"] = "SLICE-FRESH"
    base["generated_at"] = "2026-10-14T00:00:00Z"
    path = tmp_path / "POINTER-GREEN.json"
    path.write_text(json.dumps(base), encoding="utf-8")
    completed = subprocess.run(
        ["node", str(GREEN), str(path)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    cli = json.loads(completed.stdout)
    assert cli["status"] == "FAIL"
    assert any(
        item.get("transition_gate") and item["code"] in {"ADOPTION_AUTHORITY_LEGACY", "ADOPTION_AUDIT_MISSING"}
        for item in cli["errors"]
    )


def test_builder_refuses_v1_emission() -> None:
    payload = _node_json(
        """
        import { buildCurrentByteAdoptionAuthority } from "./opsx_tools/opsx-apply-implement.mjs";
        let code = "";
        try {
          buildCurrentByteAdoptionAuthority({ candidateDigest: "a".repeat(64) });
        } catch (error) {
          code = error.code;
        }
        console.log(JSON.stringify({ code }));
        """
    )
    assert payload["code"] == "ADOPTION_AUTHORITY_INVALID"


def test_node_python_candidate_digest_parity_invest_yalta() -> None:
    """Positive parity: Node computeAdoptionCandidateDigest == Python candidate_digest."""
    payload = _node_json(
        """
        import { readFileSync } from "node:fs";
        import {
          buildAdoptionCandidateManifest,
          computeAdoptionCandidateDigest,
        } from "./opsx_tools/opsx-apply-implement.mjs";
        const cr = "openspec/changes/invest-yalta-max-funnel";
        const sliceId = "SLICE-001";
        const design = JSON.parse(readFileSync(`${cr}/apply-design/APPLY-DESIGN.json`, "utf8"));
        const dr = JSON.parse(
          readFileSync(`${cr}/apply-design-review/APPLY-DESIGN-REVIEW.json`, "utf8"),
        );
        const red = JSON.parse(readFileSync(`${cr}/apply-red/${sliceId}/RED.json`, "utf8"));
        const scaffold = JSON.parse(
          readFileSync(`${cr}/apply-scaffold/${sliceId}/SCAFFOLD.json`, "utf8"),
        );
        const slice = design.slices.find((item) => item.slice_id === sliceId);
        const manifest = buildAdoptionCandidateManifest({
          changeRoot: cr,
          sliceId,
          slice,
          design,
          scaffold,
          red,
          designReview: dr,
          repoRoot: process.cwd(),
        });
        console.log(
          JSON.stringify({
            digest: computeAdoptionCandidateDigest(manifest),
            manifest,
          }),
        );
        """
    )
    node_digest = str(payload["digest"])
    manifest = payload["manifest"]
    assert isinstance(manifest, dict)
    assert node_digest == AA.candidate_digest(manifest)
    assert len(node_digest) == 64
