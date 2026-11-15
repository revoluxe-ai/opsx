"""Live-gate regression tests on the actual persisted Verify failure.

They do not invoke a model. Historical fixtures must NEVER become live
write authority even if an offline unit test rebinds their source digest.
"""
from __future__ import annotations

import json

import pytest

from agents.repair.scenario_test_repair.live_gate import (
    LiveGateBlocked,
    check_os_sdk_isolation,
    load_current_verify_state,
    prepare_live_state,
)


def test_historical_audit_rebound_to_current_digest_is_still_not_live_evidence(
    audit_context, repo_root, tmp_path,
):
    path = tmp_path / "historical-cannot-run.json"
    path.write_text(json.dumps(audit_context), encoding="utf-8")
    with pytest.raises(LiveGateBlocked, match="LIVE_AUDIT_CONTRACT_INVALID"):
        load_current_verify_state(path, repository_path=repo_root)


def test_cross_repository_state_is_not_authority(
    audit_context, repo_root, tmp_path,
):
    path = tmp_path / "wrong-target.json"
    path.write_text(json.dumps(audit_context), encoding="utf-8")
    other = tmp_path / "another-checkout"
    other.mkdir()
    with pytest.raises(LiveGateBlocked, match="LIVE_REPOSITORY_MISMATCH"):
        load_current_verify_state(path, repository_path=other)


def test_isolation_proof_does_not_default_to_true(audit_context, repo_root, tmp_path):
    with pytest.raises(LiveGateBlocked, match="LIVE_"):
        check_os_sdk_isolation(
            repo_root, audit_context, tmp_path / "no-os-sdk-receipt.json",
        )


def test_live_preflight_rejects_historical_record_even_with_fake_proof(
    audit_context, repo_root, tmp_path,
):
    path = tmp_path / "historical.json"
    path.write_text(json.dumps(audit_context), encoding="utf-8")
    proof = tmp_path / "isolation.json"
    proof.write_text("{}", encoding="utf-8")
    with pytest.raises(LiveGateBlocked, match="LIVE_AUDIT_CONTRACT_INVALID"):
        prepare_live_state(
            path, repository_path=repo_root, isolation_proof_path=proof,
        )
