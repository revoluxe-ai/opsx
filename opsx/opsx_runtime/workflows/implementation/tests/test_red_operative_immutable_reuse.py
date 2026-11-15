"""Регрессия: operative RED rewalk без live pytest при неизменных frozen bytes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from workflows.implementation.red_operative_validation import validate_red_operative
from workflows.repo_paths import resolve_opsx_repository_root

REPO = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])
FIXTURE_RED = REPO / ".cursor" / "commands" / "_fixtures" / "opsx-apply-red" / "03-red-proven.RED.json"
RED_CLI = REPO / "opsx_tools" / "opsx-apply-red.mjs"


def _run_red_cli(red_path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        ["node", str(RED_CLI), str(red_path)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(proc.stdout)


def _stamp_operative_transition(red_path: Path) -> None:
    """Добавить ``operative_red_transition`` из свежего PASS transition gate."""
    body = _run_red_cli(red_path)
    assert body["status"] == "PASS", body
    decision = body["transition_decision"]
    red = json.loads(red_path.read_text(encoding="utf-8"))
    red["operative_red_transition"] = {
        "verifier_id": "opsx-apply-red-transition-gate",
        "permitted_transition": red["status"],
        "decision_digest": decision["decision_digest"],
        "test_content_identity": decision.get("test_content_identity"),
    }
    red_path.write_text(json.dumps(red, indent=2), encoding="utf-8")


@pytest.fixture(name="stamped_red_path")
def fixture_stamped_red_path(tmp_path: Path) -> Path:
    """Копия golden RED с валидным operative stamp (вне openspec)."""
    dest = tmp_path / "RED.json"
    dest.write_text(FIXTURE_RED.read_text(encoding="utf-8"), encoding="utf-8")
    _stamp_operative_transition(dest)
    return dest


def test_unchanged_identity_skips_live_pytest(stamped_red_path: Path) -> None:
    """Принятый stamp + те же байты frozen-тестов — без live pytest."""
    gate = validate_red_operative(stamped_red_path, repo_root=REPO)
    assert gate.ok, gate.errors
    assert gate.live_pytest_skipped is True


def test_missing_operative_stamp_runs_live_pytest(tmp_path: Path) -> None:
    """Без ``operative_red_transition`` short path запрещён."""
    dest = tmp_path / "RED-no-stamp.json"
    dest.write_text(FIXTURE_RED.read_text(encoding="utf-8"), encoding="utf-8")
    gate = validate_red_operative(dest, repo_root=REPO)
    assert gate.ok, gate.errors
    assert gate.live_pytest_skipped is False


def test_r1_obligation_mismatch_refuses_live_pytest_skip(stamped_red_path: Path) -> None:
    """Stamp и identity совпадают, но R1 obligation не проходит — нужен live pytest."""
    red = json.loads(stamped_red_path.read_text(encoding="utf-8"))
    red["behavior_obligation_identity"]["obligation_digest"] = (
        "0" * 64
    )
    stamped_red_path.write_text(json.dumps(red, indent=2), encoding="utf-8")
    gate = validate_red_operative(stamped_red_path, repo_root=REPO)
    assert gate.live_pytest_skipped is False


def test_changed_test_bytes_refuse_skip(stamped_red_path: Path) -> None:
    """Дрейф байтов frozen-теста — operative FAIL, short path недоступен."""
    test_rel = "tests/unit/fixtureHydrateCoordinatorRed.test.mjs"
    test_path = REPO / test_rel
    original = test_path.read_bytes()
    try:
        _ = test_path.write_bytes(original + b"\n")
        gate = validate_red_operative(stamped_red_path, repo_root=REPO)
        assert not gate.ok
        assert gate.live_pytest_skipped is False
    finally:
        _ = test_path.write_bytes(original)
