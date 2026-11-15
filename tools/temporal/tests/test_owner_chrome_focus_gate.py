"""Tests: owner-chrome-focus-gate is retired (no Owner GO pause)."""

from __future__ import annotations

import json
from pathlib import Path

from owner_chrome_focus_gate import (
    RETIRED_SKIP_REASON,
    STEP_ID,
    TERMINAL_STATUS,
    evaluate_pre_run_gate,
    focus_gate_page_file_url,
    focus_gate_page_path,
    needs_owner_chrome_focus_gate,
    run_owner_chrome_focus_gate,
)
from owner_browser_check_gate import DEFAULT_PLAY_URL
from tempo_operator import get_pipeline


def _smoke_assignment(**overrides) -> dict:
    base = {
        "required_check_id": "mock-change.resume×smoke_acceptance",
        "capability_id": "smoke_acceptance",
        "executor": "opsx-browser-check",
        "assignment_epoch": 3,
    }
    base.update(overrides)
    return base


def _write_ledger(tmp_path: Path, assignment: dict | None) -> None:
    verify = tmp_path / "openspec" / "changes" / "mock-change" / "verify"
    verify.mkdir(parents=True)
    ledger = {
        "change_id": "mock-change",
        "plan_id": "vp-mock",
        "ledger_status": "ACTIVE",
        "current_assignment": assignment,
        "checks": [],
    }
    (verify / "VERIFICATION-LEDGER.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_focus_gate_asset_exists():
    path = focus_gate_page_path()
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "GO" in text
    assert "Chrome" in text
    assert DEFAULT_PLAY_URL in text


def test_focus_gate_page_file_url():
    url = focus_gate_page_file_url()
    assert url.startswith("file://")
    assert "owner-chrome-focus-gate.html" in url


def test_needs_owner_chrome_focus_gate_retired():
    assert needs_owner_chrome_focus_gate(_smoke_assignment()) is False
    assert needs_owner_chrome_focus_gate(
        {"capability_id": "frontend_tests", "executor": "opsx-run-tests"}
    ) is False
    assert needs_owner_chrome_focus_gate(
        {"required_check_id": "c×smoke_acceptance", "capability_id": "x", "executor": "y"}
    ) is False
    assert needs_owner_chrome_focus_gate(None) is False


def test_run_gate_always_skipped(tmp_path: Path):
    assignment = _smoke_assignment()
    result = run_owner_chrome_focus_gate(
        repo=tmp_path, change="mock-change", assignment=assignment
    )
    assert result["step_id"] == STEP_ID
    assert result["skipped"] is True
    assert result["reason"] == RETIRED_SKIP_REASON
    assert result["required_check_id"] == assignment["required_check_id"]


def test_evaluate_pre_run_gate_never_blocks_smoke(tmp_path: Path):
    _write_ledger(tmp_path, _smoke_assignment())
    assert evaluate_pre_run_gate(tmp_path, "mock-change") is None


def test_evaluate_pre_run_gate_none_for_frontend(tmp_path: Path):
    _write_ledger(
        tmp_path,
        {
            "required_check_id": "mock×frontend_tests",
            "capability_id": "frontend_tests",
            "executor": "opsx-run-tests",
            "assignment_epoch": 1,
        },
    )
    assert evaluate_pre_run_gate(tmp_path, "mock-change") is None


def test_pipeline_has_no_owner_go_pre_assignment_step():
    spec = get_pipeline("verification")
    steps = spec.get("pre_assignment_steps") or []
    assert steps == []
    assert TERMINAL_STATUS not in (spec.get("process_fail_statuses") or [])


def test_live_change_smoke_ledger_does_not_pre_run_gate():
    repo = Path(__file__).resolve().parents[2]
    change = "fix-np-paid-choice-newgame-advance"
    ledger = repo / "openspec" / "changes" / change / "verify" / "VERIFICATION-LEDGER.json"
    if not ledger.is_file():
        return
    assert evaluate_pre_run_gate(repo, change) is None
