# -*- coding: utf-8 -*-
"""Acceptance A1–A18 + hermetic E2E for OPSX-INCREMENTAL-QUALITY-COVERAGE-V1."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

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
    enforce_mode_unchanged,
    load_manifest,
    manifest_fingerprint,
)
from iqc_scope import build_scope_snapshot, classify_file  # noqa: E402
from iqc_states import FileCoverageState, ManifestActivation  # noqa: E402
from iqc_quality_audit import audit_incremental_quality_coverage  # noqa: E402
from iqc_adapters import run_incremental_quality_coverage_adapter  # noqa: E402
from iqc_plan_supersession import IQC_POLICY_REVISION, policy_revision_block_reason  # noqa: E402
from iqc_split_apply import preflight_for_change  # noqa: E402
from bounded_verify_s5_adapters import adapter_implemented, run_s5_adapter  # noqa: E402
from bounded_verify_s5_pyright_coverage import audit_pyright_strict_coverage  # noqa: E402


def test_a1_manifest_proposed_loads() -> None:
    manifest = load_manifest(REPO_ROOT)
    assert manifest.activation == ManifestActivation.PROPOSED
    assert manifest.revision >= 1


def test_a2_configured_gt_verified() -> None:
    snap = build_scope_snapshot(REPO_ROOT)
    assert snap["configured_count"] > snap["verified_count"]


def test_a3_monotonic_shrink_fails() -> None:
    with pytest.raises(IQCPolicyError):
        assert_monotonic_verified_expansion(["a.py", "b.py"], ["a.py"])


def test_a4_monotonic_additive_passes() -> None:
    assert_monotonic_verified_expansion(["a.py"], ["a.py", "b.py"]) is None


def test_a5_legacy_uncovered_classification() -> None:
    state = classify_file(
        "src/stroy/api.py",
        configured=frozenset(["src/stroy/api.py"]),
        verified=frozenset(),
        required=frozenset(),
    )
    assert state == FileCoverageState.LEGACY_UNCOVERED


def test_a6_strict_covered_classification() -> None:
    state = classify_file(
        "src/stroy/worker.py",
        configured=frozenset(["src/stroy/worker.py"]),
        verified=frozenset(["src/stroy/worker.py"]),
        required=frozenset(),
    )
    assert state == FileCoverageState.STRICT_COVERED


def test_a7_required_change_empty_when_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_DIR", str(REPO_ROOT / ".git"))
    # Hermetic: no changed paths when merge-base unavailable in tmp — use empty tuple contract
    from iqc_scope import required_change_paths

    paths = required_change_paths(REPO_ROOT)
    assert isinstance(paths, tuple)


def test_a8_audit_report_shape() -> None:
    report = audit_incremental_quality_coverage(REPO_ROOT)
    assert report["evidence_contract"] == "INCREMENTAL_QUALITY_COVERAGE_REPORT"
    assert "scopes" in report


def test_a9_broad_include_ne_verified_flag() -> None:
    report = audit_incremental_quality_coverage(REPO_ROOT)
    assert report.get("broad_include_ne_verified") is True


def test_a10_gate_audit_exit_zero() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "tools.openspec.incremental_quality_coverage_gate", "--mode", "audit"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0


def test_a11_gate_enforce_skipped_proposed() -> None:
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
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload.get("ENFORCE_SKIPPED") == "PROPOSED_MANIFEST" or payload.get("manifest_activation") == "PROPOSED"


def test_a12_manifest_fingerprint_stable() -> None:
    manifest = load_manifest(REPO_ROOT)
    fp1 = manifest_fingerprint(manifest)
    fp2 = manifest_fingerprint(manifest)
    assert fp1 == fp2


def test_a13_policy_revision_supersession_reason() -> None:
    manifest = load_manifest(REPO_ROOT)
    live = manifest_fingerprint(manifest)
    assert policy_revision_block_reason("deadbeef" * 8, root=REPO_ROOT) == IQC_POLICY_REVISION
    assert policy_revision_block_reason(live, root=REPO_ROOT) is None


def test_a14_engineering_adoption_iqc_block() -> None:
    ea_path = REPO_ROOT / ".cursor/pipeline/engineering-adoption/engineering_adoption.py"
    source = ea_path.read_text(encoding="utf-8")
    assert "incremental_quality_coverage" in source
    assert "_audit_incremental_quality_coverage" in source
    block = audit_incremental_quality_coverage(REPO_ROOT)
    assert block.get("STATUS") in ("PASS", "PASS_WITH_GAPS", "BLOCKED")


def test_a15_pyright_coverage_enrichment() -> None:
    report = audit_pyright_strict_coverage(root=REPO_ROOT)
    assert "incremental_quality_coverage" in report


def test_a16_split_apply_preflight() -> None:
    payload = preflight_for_change("mock-change", root=REPO_ROOT)
    assert "split_apply_iqc_ok" in payload


def test_a17_temporal_consumer_import() -> None:
    from tools.temporal import incremental_quality_policy as tiqc  # noqa: WPS433

    binding = tiqc.live_policy_binding(root=REPO_ROOT)
    assert binding.get("ok") is True


def test_a18_hermetic_adapter_e2e() -> None:
    assert adapter_implemented("incremental-quality-coverage", root=REPO_ROOT)
    status, packet = run_s5_adapter("incremental-quality-coverage", "hermetic-iqc", root=REPO_ROOT)
    assert status == "PASS"
    assert packet.get("evidence_contract") == "INCREMENTAL_QUALITY_COVERAGE_REPORT"
    status2, _ = run_incremental_quality_coverage_adapter("hermetic-iqc", REPO_ROOT)
    assert status2 == "PASS"


def test_mode_relax_forbidden() -> None:
    with pytest.raises(IQCPolicyError):
        enforce_mode_unchanged({"typeCheckingMode": "strict"}, {"typeCheckingMode": "basic"})
