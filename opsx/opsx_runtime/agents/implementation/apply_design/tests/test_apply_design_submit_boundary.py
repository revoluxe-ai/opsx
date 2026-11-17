"""Граница submit/publish APPLY-DESIGN: staging, CAS, receipt."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agents.implementation.apply_design.capability import build_apply_design_manifest
from agents.implementation.apply_design.tool_runtime import ApplyDesignToolRuntime
from workflows.artifact_paths import apply_design_artifact_path, apply_design_hop_receipt_path
from workflows.implementation.apply_design_hop_receipt import (
    DesignSubmissionRecord,
    decide_design_hop,
    design_hop_receipt_recovery_allowed,
    hop_receipt_present_and_valid,
    maybe_recover_design_hop_receipt,
    recover_hop_receipt_if_missing,
)
from workflows.implementation.design_machine_validation import (
    DesignDependentSnapshotResult,
    DesignMachineGateResult,
)


def _seed_change(root: Path, change_id: str) -> None:
    base = root / "openspec" / "changes" / change_id
    base.mkdir(parents=True)
    (base / "tasks.md").write_text("- [ ] t1\n", encoding="utf-8")
    (base / "design.md").write_text("# design\n", encoding="utf-8")


def _seed_capabilities(root: Path) -> None:
    cap = root / "opsx" / "project"
    cap.mkdir(parents=True)
    (cap / "capabilities.yaml").write_text(
        "product:\n  source_roots:\n    - src/stroy\n",
        encoding="utf-8",
    )


def _ready_body(change_id: str, *, tag: str = "a") -> dict[str, object]:
    return {
        "schema": "opsx.apply_design",
        "schema_version": 4,
        "change_id": change_id,
        "status": "APPLY_DESIGN_READY",
        "generated_at": "2026-11-12T00:00:00Z",
        "chat_history_required": False,
        "write_boundary": {"allowed_paths": []},
        "artifact_identity": {"machine_path": f"openspec/changes/{change_id}/apply-design/APPLY-DESIGN.json"},
        "next_stage": "APPLY_DESIGN_REVIEW",
        "planning_identity": {"planning_digest": f"plan-{tag}"},
        "protected_context_identity": {"content_digest": f"ctx-{tag}"},
        "reuse_decisions": [{"decision": "new", "target": "x"}],
        "composition_completeness": {"invariant_classifications": [{"id": "r1"}]},
        "blockers": [],
        "slices": [],
    }


def _patch_publish_ok() -> tuple:
    return (
        patch(
            "agents.implementation.apply_design.tool_runtime.validate_apply_design_at_path_async",
            new_callable=AsyncMock,
            return_value=DesignMachineGateResult(ok=True, status="PASS", exit_code=0, errors=[]),
        ),
        patch(
            "agents.implementation.apply_design.tool_runtime.evaluate_apply_design_snapshot_from_body_async",
            new_callable=AsyncMock,
            return_value=DesignDependentSnapshotResult(
                ok=True,
                code=None,
                frozen_digest="f",
                current_digest="f",
            ),
        ),
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _seed_capabilities(tmp_path)
    _seed_change(tmp_path, "visual")
    return tmp_path


@pytest.mark.asyncio
async def test_incomplete_json_does_not_replace_full_design(repo: Path) -> None:
    path = apply_design_artifact_path(repo, "visual")
    path.parent.mkdir(parents=True)
    full = _ready_body("visual", tag="full")
    path.write_text(json.dumps(full), encoding="utf-8")
    before = path.read_bytes()
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)
    stub = json.dumps({"status": "APPLY_DESIGN_READY", "change_id": "visual"})
    out = await rt.submit_apply_design(
        {"content": stub, "base_digest": rt.current_revision_digest()},
        None,
    )
    assert out.get("code") == "DESIGN_INCOMPLETE"
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_machine_gate_failure_returns_validator_errors(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)
    gate_errors = [
        {
            "code": "next_stage",
            "message": "APPLY_DESIGN_READY requires next_stage=APPLY_DESIGN_REVIEW",
        }
    ]
    with patch(
        "agents.implementation.apply_design.tool_runtime.validate_apply_design_at_path_async",
        new_callable=AsyncMock,
        return_value=DesignMachineGateResult(
            ok=False,
            status="FAIL",
            exit_code=1,
            errors=gate_errors,
        ),
    ):
        out = await rt.submit_apply_design(
            {
                "content": json.dumps(_ready_body("visual")),
                "base_digest": rt.current_revision_digest(),
            },
            None,
        )
    assert out.get("ok") is False
    assert out.get("code") == "DESIGN_MACHINE_GATE_FAILED"
    assert out.get("errors") == gate_errors


@pytest.mark.asyncio
async def test_two_sequential_valid_submits_same_hop(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)
    body1 = _ready_body("visual", tag="1")
    body2 = _ready_body("visual", tag="2")
    gate_patch, snap_patch = _patch_publish_ok()
    with gate_patch, snap_patch:
        out1 = await rt.submit_apply_design(
            {
                "content": json.dumps(body1),
                "base_digest": rt.current_revision_digest(),
            },
            None,
        )
        assert out1.get("ok") is True
        rev1 = str(out1.get("revision_digest"))
        out2 = await rt.submit_apply_design(
            {
                "content": json.dumps(body2),
                "base_digest": rev1,
            },
            None,
        )
    assert out2.get("ok") is True
    assert out2.get("revision_digest") != rev1


@pytest.mark.asyncio
async def test_external_concurrent_write_conflicts(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    path = apply_design_artifact_path(repo, "visual")
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    rt = ApplyDesignToolRuntime(manifest)
    foreign = b'{"status":"APPLY_DESIGN_READY","foreign":true}'
    path.write_bytes(foreign)
    out = await rt.submit_apply_design(
        {
            "content": json.dumps(_ready_body("visual")),
            "base_digest": rt.current_revision_digest(),
        },
        None,
    )
    assert out.get("code") == "DESIGN_DIGEST_CONFLICT"


def test_stale_snapshot_forbids_completion() -> None:
    decision = decide_design_hop(
        submission=DesignSubmissionRecord(ok=True, unchanged=False, sha256="a", code=None),
        artifact_present=True,
        gate_ok=True,
        snapshot_ok=False,
    )
    assert decision.status == "BLOCKED"
    assert decision.code == "STALE_APPLY_DESIGN"


@pytest.mark.asyncio
async def test_blocked_failed_submit_does_not_recover_receipt(repo: Path) -> None:
    """Валидный канон + failed submit: BLOCKED, receipt C не создаётся."""
    path = apply_design_artifact_path(repo, "visual")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_ready_body("visual")), encoding="utf-8")
    receipt = apply_design_hop_receipt_path(repo, "visual")
    assert not receipt.is_file()
    decision = decide_design_hop(
        submission=DesignSubmissionRecord(
            ok=False,
            unchanged=False,
            sha256=None,
            code="DESIGN_INCOMPLETE",
        ),
        artifact_present=True,
        gate_ok=True,
        snapshot_ok=True,
    )
    assert decision.status == "BLOCKED"
    assert not design_hop_receipt_recovery_allowed(
        decision=decision,
        gate_ok=True,
        snapshot_ok=True,
    )
    recovered = await maybe_recover_design_hop_receipt(
        decision=decision,
        change_id="visual",
        repo_root=repo,
        gate_ok=True,
        snapshot_ok=True,
        attempt=1,
    )
    assert recovered is False
    assert not receipt.is_file()


@pytest.mark.asyncio
async def test_recover_receipt_without_model(repo: Path) -> None:
    path = apply_design_artifact_path(repo, "visual")
    path.parent.mkdir(parents=True)
    body = _ready_body("visual")
    path.write_text(json.dumps(body), encoding="utf-8")
    receipt = apply_design_hop_receipt_path(repo, "visual")
    assert not receipt.is_file()
    ok = await recover_hop_receipt_if_missing(
        change_id="visual",
        repo_root=repo,
        gate_ok=True,
        snapshot_ok=True,
        attempt=1,
    )
    assert ok is True
    assert hop_receipt_present_and_valid(change_id="visual", repo_root=repo)
    assert receipt.is_file()


@pytest.mark.asyncio
async def test_reuse_check_does_not_change_ab_bytes(repo: Path) -> None:
    path = apply_design_artifact_path(repo, "visual")
    path.parent.mkdir(parents=True)
    raw = json.dumps(_ready_body("visual"), indent=2) + "\n"
    path.write_text(raw, encoding="utf-8")
    before = path.read_bytes()
    decision = decide_design_hop(
        submission=None,
        artifact_present=True,
        gate_ok=True,
        snapshot_ok=True,
    )
    assert decision.status == "NO_CHANGE"
    assert design_hop_receipt_recovery_allowed(
        decision=decision,
        gate_ok=True,
        snapshot_ok=True,
    )
    _ = await recover_hop_receipt_if_missing(
        change_id="visual",
        repo_root=repo,
        gate_ok=True,
        snapshot_ok=True,
    )
    assert path.read_bytes() == before
