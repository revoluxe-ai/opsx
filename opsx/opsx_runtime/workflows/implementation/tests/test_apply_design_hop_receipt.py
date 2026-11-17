"""Решения APPLY_DESIGN и receipt не меняют байты канона."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from workflows.artifact_paths import apply_design_artifact_path, apply_design_hop_receipt_path
from workflows.implementation.apply_design_hop_receipt import (
    DesignSubmissionRecord,
    decide_design_hop,
    design_hop_effective_status,
    receipt_matches,
    remember_design_submission,
    write_hop_receipt,
)


def test_valid_artifact_without_receipt_is_reuse() -> None:
    decision = decide_design_hop(
        submission=None,
        artifact_present=True,
        gate_ok=True,
        snapshot_ok=True,
    )
    assert decision.status == "NO_CHANGE"
    assert decision.code is None


def test_missing_artifact_without_submit_is_blocked() -> None:
    decision = decide_design_hop(
        submission=None,
        artifact_present=False,
        gate_ok=False,
        snapshot_ok=False,
    )
    assert decision.status == "BLOCKED"
    assert decision.code == "DESIGN_NOT_SUBMITTED"


def test_changed_bytes_do_not_match_receipt(tmp_path: Path) -> None:
    change = "demo"
    path = apply_design_artifact_path(tmp_path, change)
    path.parent.mkdir(parents=True)
    body = {"status": "APPLY_DESIGN_READY", "planning_identity": {"planning_digest": "a"}, "protected_context_identity": {"content_digest": "b"}}
    path.write_text(json.dumps(body), encoding="utf-8")
    before = path.read_bytes()
    receipt_path = apply_design_hop_receipt_path(tmp_path, change)
    receipt_path.parent.mkdir(parents=True)
    receipt = {
        "schema": "opsx.idempotency_receipt",
        "schema_version": 1,
        "status": "PASS",
        "artifacts": [{"path": path.relative_to(tmp_path).as_posix(), "sha256": "0" * 64}],
    }
    assert receipt_matches(receipt, artifact_path=path, repo_root=tmp_path) is False
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_receipt_write_does_not_change_artifact(tmp_path: Path) -> None:
    change = "demo"
    path = apply_design_artifact_path(tmp_path, change)
    path.parent.mkdir(parents=True)
    body = {
        "status": "APPLY_DESIGN_READY",
        "planning_identity": {"planning_digest": "abc"},
        "protected_context_identity": {"content_digest": "def"},
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    await write_hop_receipt(change_id=change, repo_root=tmp_path, design=body, attempt=1)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    stored = json.loads(apply_design_hop_receipt_path(tmp_path, change).read_text(encoding="utf-8"))
    assert stored["artifacts"][0]["sha256"] == before
    assert "slices" not in stored


def test_failed_submit_blocks() -> None:
    decision = decide_design_hop(
        submission=DesignSubmissionRecord(ok=False, unchanged=False, sha256=None, code="INVALID_JSON"),
        artifact_present=True,
        gate_ok=False,
        snapshot_ok=True,
    )
    assert decision.status == "BLOCKED"
    assert decision.code == "INVALID_JSON"


def test_rejected_candidate_keeps_accepted_publish() -> None:
    accepted = DesignSubmissionRecord(
        ok=True, unchanged=False, sha256="a" * 64, code=None
    )
    rejected = DesignSubmissionRecord(
        ok=False,
        unchanged=False,
        sha256=None,
        code="INVALID_ARGS",
        message="content must be JSON",
    )
    kept, hop = remember_design_submission(accepted, rejected)
    assert kept is not None and kept.ok
    assert hop.ok
    assert hop.sha256 == "a" * 64
    decision = decide_design_hop(
        submission=hop,
        artifact_present=True,
        gate_ok=True,
        snapshot_ok=True,
    )
    assert decision.status == "APPLY_COMPLETE"
    assert design_hop_effective_status("ESCALATE_ARBITER", decision.status) == "APPLY_COMPLETE"


def test_passing_canon_outranks_arbiter_escalation() -> None:
    assert design_hop_effective_status("ESCALATE_ARBITER", "NO_CHANGE") == "NO_CHANGE"
    assert design_hop_effective_status("BLOCKED", "APPLY_COMPLETE") == "APPLY_COMPLETE"
    assert design_hop_effective_status("ESCALATE_ARBITER", "BLOCKED") == "ESCALATE_ARBITER"


def test_successful_submit_blocked_when_snapshot_stale() -> None:
    decision = decide_design_hop(
        submission=DesignSubmissionRecord(ok=True, unchanged=False, sha256="abc", code=None),
        artifact_present=True,
        gate_ok=True,
        snapshot_ok=False,
    )
    assert decision.status == "BLOCKED"
    assert decision.code == "STALE_APPLY_DESIGN"
