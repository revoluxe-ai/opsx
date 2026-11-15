"""Negative acceptance tests for Channel Impact Guard V0."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD_DIR = REPO_ROOT / ".cursor" / "pipeline" / "channel-impact"
import sys

sys.path.insert(0, str(GUARD_DIR))
from channel_impact_guard import (  # noqa: E402
    ChannelImpactGuardError,
    assert_planning_ready,
    evaluate_diff_scope,
    run_archive_verify_gate,
)

GUARD_CLI = GUARD_DIR / "channel_impact_guard.py"


def _minimal_opsx_tree(root: Path) -> None:
    caps = root / "opsx/project"
    caps.mkdir(parents=True)
    (caps / "capabilities.yaml").write_text(
        (REPO_ROOT / "opsx/project/capabilities.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def _init_git(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def _commit_all(root: Path, msg: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=root, check=True, capture_output=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()


def _scenario_receipt(change: Path, change_id: str) -> None:
    sc = change / "scenario-coverage"
    sc.mkdir(parents=True)
    plan = sc / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    plan_hash = hashlib.sha256(plan.read_bytes()).hexdigest()
    receipt = {
        "SCHEMA_VERSION": 1,
        "SCENARIO_COVERAGE_RECEIPT": True,
        "CHANGE_ID": change_id,
        "MODE": "plan",
        "PLAN_STATUS": "ready",
        "MATERIALIZATION_STATUS": "COMMITTED",
        "PLAN_CONTENT_HASH": plan_hash,
        "INPUTS": {"SPEC_REVISION": []},
    }
    (sc / "plan.receipt.json").write_text(json.dumps(receipt), encoding="utf-8")


def _artifact(
    change_id: str,
    head: str,
    *,
    telegram_only: bool = False,
    fake_pa: bool = False,
) -> dict:
    decisions = {
        "telegram": {"decision": "CHANGED", "scope_summary": "tg"},
    }
    if not telegram_only:
        decisions["max"] = {
            "decision": "NOT_AFFECTED",
            "justification": "max untouched in this change",
        }
    doc = {
        "schema_version": "channel-impact-guard-v0",
        "change_id": change_id,
        "applicability": "APPLICABLE",
        "mandatory_channel_decisions": decisions,
        "product_acceptance_evidence": {
            "planning_status": "READY",
            "git_revision": head,
            "scenario_coverage_receipt": "scenario-coverage/plan.receipt.json",
        },
    }
    if fake_pa:
        doc["product_acceptance"] = {"required": True, "status": "PASS", "scenario_evidence_refs": ["/fake"]}
    return doc


def test_rejects_telegram_only_without_max_decision(tmp_path: Path) -> None:
    _minimal_opsx_tree(tmp_path)
    change = tmp_path / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "spec.md").write_text("telegram", encoding="utf-8")
    _init_git(tmp_path)
    head = _commit_all(tmp_path, "init")
    (change / "channel-impact-guard.json").write_text(
        json.dumps(_artifact("demo", head, telegram_only=True)),
        encoding="utf-8",
    )
    with pytest.raises(ChannelImpactGuardError) as exc:
        assert_planning_ready("demo", root=tmp_path)
    assert exc.value.code == "MANDATORY_CHANNEL_DECISION_MISSING"


def test_rejects_booking_without_assessment(tmp_path: Path) -> None:
    _minimal_opsx_tree(tmp_path)
    booking = tmp_path / "src/stroy/booking/service.py"
    booking.parent.mkdir(parents=True)
    booking.write_text("x", encoding="utf-8")
    _init_git(tmp_path)
    _commit_all(tmp_path, "base")
    booking.write_text("y", encoding="utf-8")
    _commit_all(tmp_path, "head")
    with pytest.raises(ChannelImpactGuardError) as exc:
        evaluate_diff_scope(base="HEAD~1", head="HEAD", root=tmp_path)
    assert exc.value.code == "UNRESOLVED_NO_CHANGE_CONTOUR"


def test_rejects_no_change_without_justification(tmp_path: Path) -> None:
    _minimal_opsx_tree(tmp_path)
    change = tmp_path / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "spec.md").write_text("telegram", encoding="utf-8")
    (change / "channel-impact-guard.json").write_text(
        json.dumps(
            {
                "schema_version": "channel-impact-guard-v0",
                "change_id": "demo",
                "applicability": "NO_CHANGE",
                "mandatory_channel_decisions": {
                    "telegram": {"decision": "NO_CHANGE", "justification": "short"},
                    "max": {"decision": "NO_CHANGE", "justification": "short"},
                },
            }
        ),
        encoding="utf-8",
    )
    _init_git(tmp_path)
    _commit_all(tmp_path, "init")
    with pytest.raises(ChannelImpactGuardError) as exc:
        assert_planning_ready("demo", root=tmp_path)
    assert exc.value.code == "CHANNEL_DECISION_UNJUSTIFIED"


def test_self_declared_pa_pass_does_not_bypass_receipt(tmp_path: Path) -> None:
    _minimal_opsx_tree(tmp_path)
    change = tmp_path / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "spec.md").write_text("telegram", encoding="utf-8")
    _init_git(tmp_path)
    head = _commit_all(tmp_path, "init")
    (change / "channel-impact-guard.json").write_text(
        json.dumps(_artifact("demo", head, fake_pa=True)),
        encoding="utf-8",
    )
    with pytest.raises(ChannelImpactGuardError) as exc:
        assert_planning_ready("demo", root=tmp_path)
    assert exc.value.code == "PA_SELF_DECLARED_FORBIDDEN"


def test_rejects_stale_pa_revision(tmp_path: Path) -> None:
    _minimal_opsx_tree(tmp_path)
    change = tmp_path / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "spec.md").write_text("telegram", encoding="utf-8")
    _scenario_receipt(change, "demo")
    _init_git(tmp_path)
    head = _commit_all(tmp_path, "init")
    art = _artifact("demo", "deadbeef00000000000000000000000000000000")
    (change / "channel-impact-guard.json").write_text(json.dumps(art), encoding="utf-8")
    webhooks = tmp_path / "src/stroy/webhooks/telegram.py"
    webhooks.parent.mkdir(parents=True)
    webhooks.write_text("x", encoding="utf-8")
    _commit_all(tmp_path, "touch")
    from channel_impact_guard import evaluate_change, load_channel_impact_registry

    with pytest.raises(ChannelImpactGuardError) as exc:
        evaluate_change(
            "demo",
            surfaces_touched=("src/stroy/webhooks/telegram.py",),
            business_surfaces=False,
            root=tmp_path,
            registry=load_channel_impact_registry(tmp_path),
            head_sha=head,
        )
    assert exc.value.code == "PA_EVIDENCE_STALE_REVISION"


def test_verify_gate_cli_rejects_missing_artifact(tmp_path: Path) -> None:
    _minimal_opsx_tree(tmp_path)
    change = tmp_path / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "spec.md").write_text("telegram", encoding="utf-8")
    webhooks = tmp_path / "src/stroy/webhooks/telegram.py"
    webhooks.parent.mkdir(parents=True)
    _init_git(tmp_path)
    _commit_all(tmp_path, "base")
    webhooks.write_text("z", encoding="utf-8")
    (change / "spec.md").write_text("telegram\n", encoding="utf-8")
    _commit_all(tmp_path, "head")
    proc = subprocess.run(
        [
            sys.executable,
            str(GUARD_CLI),
            "verify-gate",
            "--change",
            "demo",
            "--base",
            "HEAD~1",
            "--head",
            "HEAD",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "UNRESOLVED" in proc.stderr or "MISSING" in proc.stderr


def test_infrastructure_diff_not_applicable(tmp_path: Path) -> None:
    _minimal_opsx_tree(tmp_path)
    infra = tmp_path / ".github/workflows/foo.yml"
    infra.parent.mkdir(parents=True)
    _init_git(tmp_path)
    _commit_all(tmp_path, "base")
    infra.write_text("on: push\n", encoding="utf-8")
    _commit_all(tmp_path, "head")
    result = evaluate_diff_scope(base="HEAD~1", head="HEAD", root=tmp_path)
    assert result.status == "NOT_APPLICABLE"


def test_archive_verify_gate_pass_with_receipt(tmp_path: Path) -> None:
    _minimal_opsx_tree(tmp_path)
    change = tmp_path / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "spec.md").write_text("telegram\n", encoding="utf-8")
    _scenario_receipt(change, "demo")
    webhooks = tmp_path / "src/stroy/webhooks/telegram.py"
    webhooks.parent.mkdir(parents=True)
    _init_git(tmp_path)
    head = _commit_all(tmp_path, "base")
    (change / "channel-impact-guard.json").write_text(
        json.dumps(_artifact("demo", head)),
        encoding="utf-8",
    )
    webhooks.write_text("changed", encoding="utf-8")
    (change / "spec.md").write_text("telegram\n\n", encoding="utf-8")
    head2 = _commit_all(tmp_path, "head")
    (change / "channel-impact-guard.json").write_text(
        json.dumps(_artifact("demo", head2)),
        encoding="utf-8",
    )
    result = run_archive_verify_gate("demo", base="HEAD~1", head="HEAD", root=tmp_path)
    assert result.status == "PASS"
