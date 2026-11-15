"""Tests for owner-done-signal PP post-completion step."""

from __future__ import annotations

import json
from pathlib import Path

from owner_done_signal import (
    OWNER_DONE_MESSAGE,
    STEP_ID,
    done_page_file_url,
    done_page_path,
    is_terminal_outcome,
    run_owner_done_signal,
    run_post_completion_steps,
)
from tempo_operator import get_pipeline


def test_done_page_asset_exists():
    path = done_page_path()
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert OWNER_DONE_MESSAGE in text


def test_done_page_file_url_windows_style(tmp_path: Path):
    url = done_page_file_url(repo=tmp_path)
    assert url.startswith("file://")
    assert "owner-done-signal.html" in url


def test_is_terminal_outcome_verification_spec():
    spec = get_pipeline("verification")
    assert is_terminal_outcome("ALL_REQUIRED_CHECKS_PASS", spec)
    assert is_terminal_outcome("VERIFICATION_CHECK_FAILED", spec)
    assert is_terminal_outcome("VERIFICATION_CHECK_BLOCKED", spec)
    assert not is_terminal_outcome("RUNNING", spec)


def test_run_owner_done_signal_writes_manifest(tmp_path: Path):
    spec = get_pipeline("verification")
    result = run_owner_done_signal(
        repo=tmp_path,
        pipeline_id="verification",
        spec=spec,
        workflow_id="verification-execution-poc-real-test",
        outcome="VERIFICATION_CHECK_BLOCKED",
        resolved={"change": "mock-change"},
        operator_status="FAIL",
    )
    assert result["step_id"] == STEP_ID
    assert result["skipped"] is False
    assert OWNER_DONE_MESSAGE == result["message"]
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["browser_action"]["tool"] == "navigate_page"
    assert manifest["change"] == "mock-change"


def test_post_completion_steps_from_pipeline_spec(tmp_path: Path):
    spec = get_pipeline("verification")
    assert any(
        (s.get("id") if isinstance(s, dict) else s) == STEP_ID
        for s in spec.get("post_completion_steps", [])
    )
    results = run_post_completion_steps(
        repo=tmp_path,
        pipeline_id="verification",
        spec=spec,
        workflow_id="wf-1",
        outcome="ALL_REQUIRED_CHECKS_PASS",
        resolved={"change": "c"},
        operator_status="PASS",
    )
    assert len(results) == 1
    assert results[0]["skipped"] is False
