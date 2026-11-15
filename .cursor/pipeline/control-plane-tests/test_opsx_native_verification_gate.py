"""Regression tests for OPSX-native verification gate (false-green prevention)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_CONTROL_PLANE_TESTS = Path(__file__).resolve().parent
_REPO_ROOT = Path(__file__).resolve().parents[3]
_GATE_SCRIPT = _REPO_ROOT / "tools/openspec/run_opsx_native_verification_gate.py"

if str(_CONTROL_PLANE_TESTS) not in sys.path:
    sys.path.insert(0, str(_CONTROL_PLANE_TESTS))
_OPSX_TOOLS = _REPO_ROOT / "tools" / "openspec"
if str(_OPSX_TOOLS) not in sys.path:
    sys.path.insert(0, str(_OPSX_TOOLS))

from run_opsx_native_verification_gate import (  # noqa: E402
    committed_input_revision,
    run_gate,
)


@pytest.fixture(autouse=True)
def _restore_opsx_closure_artifacts() -> None:
    """Keep repo clean for gate tests that share the real working tree."""

    yield
    _ = subprocess.run(
        [
            "git",
            "-C",
            str(_REPO_ROOT),
            "checkout",
            "--",
            "opsx/projectization.yaml",
            "opsx/transfer/DEPENDENCY_CLOSURE_RECEIPT.json",
        ],
        check=False,
    )


def _expected_committed_input_revision() -> str:
    """Committed receipt + projectization pin at HEAD (not execution HEAD)."""

    revision = committed_input_revision(_REPO_ROOT)
    assert revision, "committed DEPENDENCY_CLOSURE_RECEIPT.json must pin target_revision at HEAD"
    return revision


def _run_gate_cli(*extra: str) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(_GATE_SCRIPT), *extra],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(proc.stdout)
    return proc.returncode, payload


def test_clean_head_gate_passes_without_head_pin_to_input_revision() -> None:
    """Committed input revision may differ from execution HEAD; gate still PASS when clean."""

    code, payload = _run_gate_cli("--skip-pytest")
    if code != 0:
        fails = [c for c in payload.get("checks", []) if c.get("status") == "FAIL"]
        pytest.fail(f"gate exit={code} failures={fails}")
    assert payload["overall_status"] == "PASS"
    assert payload["execution_head_sha"]
    assert payload["committed_input_revision"] == _expected_committed_input_revision()
    assert payload["execution_head_sha"] == subprocess.check_output(
        ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    check_ids = {c["check_id"] for c in payload["checks"]}
    assert "WORKING_TREE_CLEAN" in check_ids
    assert "INPUT_REVISION_PIN_CONSISTENCY" in check_ids
    assert "PROJECTIZATION_HEAD_PIN" not in check_ids
    assert "RECEIPT_FRESHNESS" not in check_ids
    pin = next(c for c in payload["checks"] if c["check_id"] == "INPUT_REVISION_PIN_CONSISTENCY")
    assert pin["status"] == "PASS"


def test_input_revision_pin_inconsistency_fails_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Committed receipt vs projectization pin mismatch must fail closed at HEAD."""

    import run_opsx_native_verification_gate as gate_mod

    expected = _expected_committed_input_revision()
    real_yaml = gate_mod.read_yaml_field_at_head

    def _mismatched_projectization_pin(
        root: Path, relpath: str, field: str
    ) -> str | None:
        if relpath == gate_mod.PROJECTIZATION_REL and field == "target_revision":
            return "0" * 40
        return real_yaml(root, relpath, field)

    monkeypatch.setattr(gate_mod, "read_yaml_field_at_head", _mismatched_projectization_pin)
    pin = gate_mod.check_input_revision_pin_consistency(_REPO_ROOT)
    assert pin.status == "FAIL"
    assert expected in pin.detail


def test_refresh_receipt_repair_mode_never_attests_pass() -> None:
    code, payload = _run_gate_cli("--refresh-receipt", "--skip-pytest")
    assert code == 2
    assert payload["mode"] == "repair_refresh_receipt"
    assert payload["overall_status"] == "FAIL"
    proc = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "checkout", "--", "opsx/projectization.yaml", "opsx/transfer/DEPENDENCY_CLOSURE_RECEIPT.json"],
        check=True,
    )
    assert proc.returncode == 0


def test_dirty_worktree_fails_even_when_receipt_matches_head_locally() -> None:
    receipt = _REPO_ROOT / "opsx/transfer/DEPENDENCY_CLOSURE_RECEIPT.json"
    original = receipt.read_text(encoding="utf-8")
    try:
        receipt.write_text(original + "\n", encoding="utf-8")
        report = run_gate(_REPO_ROOT, gate_id="test", run_pytest=False, python=sys.executable)
        assert report.overall_status == "FAIL"
        clean = next(c for c in report.checks if c.check_id == "WORKING_TREE_CLEAN")
        assert clean.status == "FAIL"
        unmod = next(c for c in report.checks if c.check_id == "COMMITTED_ARTIFACTS_UNMODIFIED")
        assert unmod.status == "FAIL"
    finally:
        receipt.write_text(original, encoding="utf-8")


def test_stale_receipt_on_disk_fails_committed_artifacts_check() -> None:
    """Mutate receipt to match HEAD in working tree only (simulates post-refresh without commit)."""

    code, _ = _run_gate_cli("--refresh-receipt", "--skip-pytest")
    assert code == 2
    code2, payload2 = _run_gate_cli("--skip-pytest")
    assert code2 != 0
    assert payload2["overall_status"] == "FAIL"
    unmod = next(c for c in payload2["checks"] if c["check_id"] == "COMMITTED_ARTIFACTS_UNMODIFIED")
    assert unmod["status"] == "FAIL"
    subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "checkout", "--", "opsx/projectization.yaml", "opsx/transfer/DEPENDENCY_CLOSURE_RECEIPT.json"],
        check=True,
    )
