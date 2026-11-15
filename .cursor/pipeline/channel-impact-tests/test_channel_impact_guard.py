"""Tests for Channel Impact Guard V0."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GUARD_DIR = REPO_ROOT / ".cursor" / "pipeline" / "channel-impact"
import sys

sys.path.insert(0, str(GUARD_DIR))
from channel_impact_guard import (  # noqa: E402
    ChannelImpactGuardError,
    evaluate_diff_scope,
    path_matches_globs,
)


def test_surface_glob_matches_webhook() -> None:
    assert path_matches_globs(
        "src/stroy/webhooks/telegram.py",
        ["src/stroy/webhooks/**"],
    )


def test_no_surfaces_not_applicable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _minimal_opsx_tree(tmp_path)
    (tmp_path / "README.md").write_text("x", encoding="utf-8")
    _init_git(tmp_path)
    _commit_all(tmp_path, "base")
    (tmp_path / "docs.txt").write_text("y", encoding="utf-8")
    _commit_all(tmp_path, "head")

    result = evaluate_diff_scope(base="HEAD~1", head="HEAD", root=tmp_path)
    assert result.status == "NOT_APPLICABLE"


def test_unresolved_contour_without_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _minimal_opsx_tree(tmp_path)
    webhooks = tmp_path / "src/stroy/webhooks"
    webhooks.mkdir(parents=True)
    (webhooks / "telegram.py").write_text("# x", encoding="utf-8")
    _init_git(tmp_path)
    _commit_all(tmp_path, "base")
    (webhooks / "telegram.py").write_text("# changed", encoding="utf-8")
    _commit_all(tmp_path, "head")

    with pytest.raises(ChannelImpactGuardError) as exc:
        evaluate_diff_scope(base="HEAD~1", head="HEAD", root=tmp_path)
    assert exc.value.code == "UNRESOLVED_NO_CHANGE_CONTOUR"


def test_pass_with_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    monkeypatch.chdir(tmp_path)
    _minimal_opsx_tree(tmp_path)
    change = tmp_path / "openspec/changes/demo-channel"
    change.mkdir(parents=True)
    (change / "spec.md").write_text("telegram ingress", encoding="utf-8")
    webhooks = tmp_path / "src/stroy/webhooks"
    webhooks.mkdir(parents=True)
    (webhooks / "telegram.py").write_text("# x", encoding="utf-8")
    _init_git(tmp_path)
    head = _commit_all(tmp_path, "base")
    sc = change / "scenario-coverage"
    sc.mkdir(parents=True)
    plan = sc / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    plan_hash = hashlib.sha256(plan.read_bytes()).hexdigest()
    artifact = {
        "schema_version": "channel-impact-guard-v0",
        "change_id": "demo-channel",
        "applicability": "APPLICABLE",
        "mandatory_channel_decisions": {
            "telegram": {"decision": "CHANGED", "scope_summary": "webhook"},
            "max": {"decision": "NOT_AFFECTED", "justification": "max not in slice"},
        },
        "product_acceptance_evidence": {
            "planning_status": "READY",
            "git_revision": head,
            "scenario_coverage_receipt": "scenario-coverage/plan.receipt.json",
        },
    }
    (change / "channel-impact-guard.json").write_text(json.dumps(artifact), encoding="utf-8")
    (sc / "plan.receipt.json").write_text(
        json.dumps(
            {
                "SCHEMA_VERSION": 1,
                "SCENARIO_COVERAGE_RECEIPT": True,
                "CHANGE_ID": "demo-channel",
                "MODE": "plan",
                "PLAN_STATUS": "ready",
                "MATERIALIZATION_STATUS": "COMMITTED",
                "PLAN_CONTENT_HASH": plan_hash,
                "INPUTS": {"SPEC_REVISION": []},
            }
        ),
        encoding="utf-8",
    )
    _commit_all(tmp_path, "meta")
    (webhooks / "telegram.py").write_text("# changed", encoding="utf-8")
    (change / "spec.md").write_text("telegram ingress\n", encoding="utf-8")
    head2 = _commit_all(tmp_path, "head")
    artifact["product_acceptance_evidence"]["git_revision"] = head2
    (change / "channel-impact-guard.json").write_text(json.dumps(artifact), encoding="utf-8")
    _commit_all(tmp_path, "head2")
    import subprocess

    final_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True
    ).strip()
    artifact["product_acceptance_evidence"]["git_revision"] = final_head
    (change / "channel-impact-guard.json").write_text(json.dumps(artifact), encoding="utf-8")

    result = evaluate_diff_scope(base="HEAD~2", head="HEAD", root=tmp_path)
    assert result.status == "PASS"


def _minimal_opsx_tree(root: Path) -> None:
    caps = root / "opsx/project"
    caps.mkdir(parents=True)
    (caps / "capabilities.yaml").write_text(
        (REPO_ROOT / "opsx/project/capabilities.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def _init_git(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def _commit_all(root: Path, _label: str) -> str:
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", _label],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
