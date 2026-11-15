# -*- coding: utf-8 -*-
"""P0 adversarial fixtures: OPSX IQC V1 activation readiness."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
IQC_DIR = REPO_ROOT / ".cursor/pipeline/incremental-quality-coverage"
BV_DIR = REPO_ROOT / ".cursor/pipeline/bounded-verify"

for path in (IQC_DIR, BV_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from iqc_policy import (  # noqa: E402
    IQCPolicyError,
    assert_monotonic_verified_expansion,
    load_manifest,
    manifest_fingerprint,
)
from iqc_quality_audit import audit_incremental_quality_coverage  # noqa: E402
from iqc_required_change import resolve_required_change_inventory  # noqa: E402
from iqc_scope import classify_file, paths_needing_expansion  # noqa: E402
from iqc_split_apply import preflight_for_change  # noqa: E402
from iqc_states import FileCoverageState, ManifestActivation, VerifiedScopeLayer  # noqa: E402
from iqc_plan_supersession import IQC_POLICY_REVISION, policy_revision_block_reason  # noqa: E402
from bounded_verify_s5_adapters import run_s5_adapter  # noqa: E402
from iqc_adapters import run_incremental_quality_coverage_adapter  # noqa: E402


@pytest.fixture(autouse=True)
def _skip_pyright_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPSX_IQC_SKIP_PYRIGHT_EVIDENCE", "1")


def _seed_capabilities(root: Path) -> None:
    cap = root / "opsx/project/capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx/project/capabilities.yaml", cap)


def _write_manifest(
    root: Path,
    *,
    activation: str,
    verified_include: list[str],
    strict_include: list[str] | None = None,
    strict_ignore: list[str] | None = None,
    active: bool = False,
) -> None:
    opsx = root / "opsx/incremental-quality-coverage"
    opsx.mkdir(parents=True, exist_ok=True)
    rel = "manifest.yaml" if active else "manifest.proposed.yaml"
    import yaml

    body: dict[str, Any] = {
        "schema_version": "incremental-quality-coverage-manifest-v1",
        "policy_id": "hermetic-iqc",
        "activation": activation,
        "language": "python",
        "checker": "pyright",
        "revision": 1,
        "configured_scope": {"config_path": "pyrightconfig_strict.json", "mode": "include_with_ignore"},
        "verified_scope": {"config_path": "pyrightconfig_strict_incremental.json", "mode": "explicit_include"},
        "required_change_scope": {"derivation": "in_policy_diff_subset_configured"},
    }
    (opsx / rel).write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    strict = {
        "include": strict_include or ["src/stroy"],
        "exclude": [],
        "ignore": strict_ignore or [],
        "typeCheckingMode": "strict",
    }
    (root / "pyrightconfig_strict.json").write_text(json.dumps(strict), encoding="utf-8")
    (root / "pyrightconfig_strict_incremental.json").write_text(
        json.dumps({"include": verified_include, "typeCheckingMode": "strict"}),
        encoding="utf-8",
    )


def _git_init_commit(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "iqc@test"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "iqc"], cwd=root, check=True, capture_output=True)
    _seed_capabilities(root)
    (root / "src/stroy").mkdir(parents=True, exist_ok=True)
    base = root / "src/stroy/base.py"
    base.write_text("def base() -> int:\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)


@pytest.fixture
def hermetic_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git_init_commit(root)
    _write_manifest(
        root,
        activation="PROPOSED",
        verified_include=["src/stroy/base.py"],
        strict_ignore=["src/stroy/ignored.py"],
    )
    os.environ["BOUNDED_VERIFY_COVERAGE_BASE_REF"] = "HEAD"
    return root


def test_p0_1_configured_candidate_evidence_layers(hermetic_repo: Path) -> None:
    report = audit_incremental_quality_coverage(hermetic_repo)
    scopes = report["scopes"]
    assert scopes["CONFIGURED_SCOPE_count"] >= 1
    assert scopes["CANDIDATE_VERIFIED_SCOPE_count"] == 1
    assert scopes["EVIDENCE_VERIFIED_SCOPE_count"] == 0
    assert report["verified_scope_layer"] == VerifiedScopeLayer.CANDIDATE_VERIFIED.value
    assert report["no_auto_evidence_from_config_only"] is True


def test_p0_1_pyright_success_promotes_evidence_layer(hermetic_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPSX_IQC_SKIP_PYRIGHT_EVIDENCE", raising=False)
    fake = {"ran": True, "exit_code": 0, "reason": None, "command": "pyright --project x"}
    with patch(
        "iqc_verified_evidence.run_verified_scope_pyright",
        return_value=fake,
    ):
        report = audit_incremental_quality_coverage(hermetic_repo)
    assert report["verified_scope_layer"] == VerifiedScopeLayer.EVIDENCE_VERIFIED.value
    assert report["scopes"]["EVIDENCE_VERIFIED_SCOPE_count"] == report["scopes"]["CANDIDATE_VERIFIED_SCOPE_count"]


def test_p0_2_ignore_bypass_required_change() -> None:
    state = classify_file(
        "src/stroy/ignored.py",
        configured=frozenset(),
        verified=frozenset(["src/stroy/base.py"]),
        required=frozenset(["src/stroy/ignored.py"]),
    )
    assert state == FileCoverageState.COVERAGE_EXPANSION
    expansion = paths_needing_expansion(
        ("src/stroy/ignored.py",),
        configured=frozenset(),
        verified=frozenset(["src/stroy/base.py"]),
    )
    assert expansion == ("src/stroy/ignored.py",)


def test_p0_2_worktree_lists_ignored_changed_file(hermetic_repo: Path) -> None:
    ignored = hermetic_repo / "src/stroy/ignored.py"
    ignored.write_text("x = 1\n", encoding="utf-8")
    inv = resolve_required_change_inventory(hermetic_repo)
    assert inv["status"] == "RESOLVED"
    assert "src/stroy/ignored.py" in inv["paths"]


def test_p0_3_base_unresolved_blocked_not_pass(tmp_path: Path) -> None:
    root = tmp_path / "git-no-base"
    root.mkdir()
    _write_manifest(root, activation="PROPOSED", verified_include=[])
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "iqc@test"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "iqc"], cwd=root, check=True, capture_output=True)
    (root / "src/stroy").mkdir(parents=True)
    (root / "src/stroy/x.py").write_text("x=1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "only"], cwd=root, check=True, capture_output=True)
    os.environ.pop("BOUNDED_VERIFY_COVERAGE_BASE_REF", None)
    report = audit_incremental_quality_coverage(root)
    assert report["STATUS"] == "BLOCKED"
    assert report["REASON"] == "GIT_BASE_UNRESOLVED"


def test_p0_3_inventory_rename_delete_staged_untracked(hermetic_repo: Path) -> None:
    root = hermetic_repo
    tracked = root / "src/stroy/tracked.py"
    tracked.write_text("a=1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/stroy/tracked.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add tracked"], cwd=root, check=True, capture_output=True)
    tracked.write_text("a=2\n", encoding="utf-8")
    unstaged = resolve_required_change_inventory(root)
    assert "src/stroy/tracked.py" in unstaged["paths"]
    subprocess.run(["git", "add", "src/stroy/tracked.py"], cwd=root, check=True, capture_output=True)
    staged = resolve_required_change_inventory(root)
    assert "src/stroy/tracked.py" in staged["paths"]
    subprocess.run(["git", "mv", "src/stroy/tracked.py", "src/stroy/renamed.py"], cwd=root, check=True, capture_output=True)
    renamed = resolve_required_change_inventory(root)
    assert "src/stroy/renamed.py" in renamed["paths"]
    subprocess.run(["git", "rm", "-f", "src/stroy/renamed.py"], cwd=root, check=True, capture_output=True)
    deleted = resolve_required_change_inventory(root)
    assert "src/stroy/renamed.py" not in deleted["paths"]
    untracked = root / "src/stroy/new_untracked.py"
    untracked.write_text("z=1\n", encoding="utf-8")
    with_untracked = resolve_required_change_inventory(root)
    assert "src/stroy/new_untracked.py" in with_untracked["paths"]


def test_p0_4_active_uncovered_changed_fails(hermetic_repo: Path) -> None:
    root = hermetic_repo
    (root / "opsx/incremental-quality-coverage/manifest.yaml").unlink(missing_ok=True)
    _write_manifest(
        root,
        activation="ACTIVE",
        verified_include=["src/stroy/base.py"],
        active=True,
    )
    changed = root / "src/stroy/changed.py"
    changed.write_text("y: int = 'bad'\n", encoding="utf-8")
    report = audit_incremental_quality_coverage(root)
    assert report["manifest_activation"] == ManifestActivation.ACTIVE.value
    assert report["STATUS"] == "FAIL"
    assert "src/stroy/changed.py" in report["expansion_needed"]


def test_p0_4_missing_manifest_blocked() -> None:
    with pytest.raises(IQCPolicyError):
        load_manifest(Path("/nonexistent"))


def test_p0_4_verified_expansion_passes(hermetic_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = hermetic_repo
    new = root / "src/stroy/new_ok.py"
    new.write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
    _write_manifest(
        root,
        activation="ACTIVE",
        verified_include=["src/stroy/base.py", "src/stroy/new_ok.py"],
        active=True,
    )
    monkeypatch.delenv("OPSX_IQC_SKIP_PYRIGHT_EVIDENCE", raising=False)
    fake = {"ran": True, "exit_code": 0, "reason": None, "command": "pyright --project x"}
    with patch("iqc_verified_evidence.run_verified_scope_pyright", return_value=fake):
        report = audit_incremental_quality_coverage(root)
    assert report["STATUS"] == "PASS"
    assert report["expansion_needed"] == []
    assert report["verified_scope_layer"] == VerifiedScopeLayer.EVIDENCE_VERIFIED.value


def test_p0_4_proposed_audit_only_enforce_skipped() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.openspec.incremental_quality_coverage_gate",
            "--mode",
            "enforce",
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "OPSX_IQC_SKIP_PYRIGHT_EVIDENCE": "1"},
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload.get("ENFORCE_SKIPPED") == "PROPOSED_MANIFEST" or payload.get("manifest_activation") == "PROPOSED"


def test_p0_4_pass_with_gaps_not_split_apply_ok_when_active(hermetic_repo: Path) -> None:
    root = hermetic_repo
    _write_manifest(root, activation="ACTIVE", verified_include=["src/stroy/base.py"], active=True)
    gap = root / "src/stroy/gap.py"
    gap.write_text("g=1\n", encoding="utf-8")
    report = audit_incremental_quality_coverage(root)
    assert report["STATUS"] == "FAIL"
    preflight = preflight_for_change("chg", root=root)
    assert preflight["split_apply_iqc_ok"] is False


def test_p0_5_active_pass_impossible_without_pyright_evidence(hermetic_repo: Path) -> None:
    """ACTIVE + OPSX_IQC_SKIP_PYRIGHT_EVIDENCE must not yield PASS (enforce gate contract)."""
    root = hermetic_repo
    _write_manifest(root, activation="ACTIVE", verified_include=["src/stroy/base.py"], active=True)
    report = audit_incremental_quality_coverage(root)
    assert report["STATUS"] == "FAIL"
    assert report.get("REASON") == "PYRIGHT_EVIDENCE_SKIPPED"


def test_p0_5_evidence_binding_fields(hermetic_repo: Path) -> None:
    report = audit_incremental_quality_coverage(hermetic_repo)
    binding = report["verified_scope_evidence"]["binding"]
    assert binding.get("git_head")
    assert binding.get("execution_id")
    assert binding.get("verified_config_fingerprint")
    assert binding.get("configured_config_fingerprint")
    assert "src/stroy/base.py" in binding.get("verified_path_content_hashes", {})
    assert report.get("provenance_git_head") == binding.get("git_head")
    assert report.get("execution_id") == binding.get("execution_id")


def test_p0_5_configured_scope_change_invalidates_policy_fingerprint(hermetic_repo: Path) -> None:
    manifest = load_manifest(hermetic_repo)
    before = manifest_fingerprint(manifest, root=hermetic_repo)
    strict = json.loads((hermetic_repo / "pyrightconfig_strict.json").read_text(encoding="utf-8"))
    strict["ignore"] = ["src/stroy/extra.py"]
    (hermetic_repo / "pyrightconfig_strict.json").write_text(json.dumps(strict), encoding="utf-8")
    after = manifest_fingerprint(manifest, root=hermetic_repo)
    assert before != after
    assert policy_revision_block_reason(before, root=hermetic_repo) == IQC_POLICY_REVISION


def test_p0_4_pyright_failure_not_masked_active(hermetic_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPSX_IQC_SKIP_PYRIGHT_EVIDENCE", raising=False)
    _write_manifest(hermetic_repo, activation="ACTIVE", verified_include=["src/stroy/base.py"], active=True)
    fake = {"ran": True, "exit_code": 1, "reason": None, "command": "pyright --project x"}
    with patch("iqc_verified_evidence.run_verified_scope_pyright", return_value=fake):
        report = audit_incremental_quality_coverage(hermetic_repo)
    assert report["STATUS"] == "FAIL"


def test_p0_4_config_fingerprint_supersession(hermetic_repo: Path) -> None:
    manifest = load_manifest(hermetic_repo)
    from iqc_policy import manifest_fingerprint

    live = manifest_fingerprint(manifest, root=hermetic_repo)
    assert policy_revision_block_reason("deadbeef" * 8, root=hermetic_repo) == IQC_POLICY_REVISION
    assert policy_revision_block_reason(live, root=hermetic_repo) is None


def test_p0_4_monotonic_shrink_blocked() -> None:
    with pytest.raises(IQCPolicyError):
        assert_monotonic_verified_expansion(["a.py"], [])


def test_p0_4_bounded_verify_adapter_proposed_pass(hermetic_repo: Path) -> None:
    status, packet = run_incremental_quality_coverage_adapter("hermetic", hermetic_repo)
    assert status == "PASS"
    assert packet.get("evidence_contract") == "INCREMENTAL_QUALITY_COVERAGE_REPORT"


def test_p0_4_s5_adapter_blocked_on_missing_identity(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    _seed_capabilities(root)
    status, packet = run_s5_adapter("incremental-quality-coverage", "x", root=root)
    assert status == "EXECUTION_ERROR"


def test_p0_4_audit_blocked_manifest_missing(tmp_path: Path) -> None:
    root = tmp_path / "no-manifest"
    root.mkdir()
    report = audit_incremental_quality_coverage(root)
    assert report["STATUS"] == "BLOCKED"
    assert report["REASON"] == "MANIFEST_MISSING"
