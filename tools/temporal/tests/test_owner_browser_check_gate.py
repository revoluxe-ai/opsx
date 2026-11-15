"""Tests for owner-browser-check-gate GO handshake and release UX."""

from __future__ import annotations

import json
from pathlib import Path

from owner_browser_check_gate import (
    DEFAULT_PLAY_URL,
    GO_STEP_ID,
    RELEASE_STEP_ID,
    format_chat_instruction,
    format_release_chat_message,
    is_go_acknowledged,
    is_pre_smoke_acknowledged,
    owner_chat_contains_go,
    release_page_file_url,
    release_page_path,
    run_browser_check_release,
    write_go_ack,
)
from owner_chrome_focus_gate import write_focus_ack


def _smoke_assignment(**overrides) -> dict:
    base = {
        "required_check_id": "mock-change.resume×smoke_acceptance",
        "capability_id": "smoke_acceptance",
        "executor": "opsx-browser-check",
        "assignment_epoch": 3,
    }
    base.update(overrides)
    return base


def test_release_asset_exists():
    path = release_page_path()
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "Браузерная проверка завершена" in text


def test_release_page_file_url():
    url = release_page_file_url()
    assert url.startswith("file://")
    assert "owner-browser-check-release.html" in url


def test_owner_chat_contains_go():
    assert owner_chat_contains_go("GO")
    assert owner_chat_contains_go("ok, go")
    assert owner_chat_contains_go("давай GO!")
    assert not owner_chat_contains_go("continue")
    assert not owner_chat_contains_go("")


def test_format_chat_instruction_mentions_go():
    chat = format_chat_instruction(change="mock-change", assignment=_smoke_assignment())
    assert chat["go_token"] == "GO"
    assert "GO" in chat["markdown"]
    assert "say_go_in_chat" in chat["owner_actions"]
    assert "Пока не напишете GO" in chat["markdown"]


def test_format_chat_instruction_includes_play_url_fenced_block():
    chat = format_chat_instruction(change="mock-change", assignment=_smoke_assignment())
    assert chat["play_url"] == DEFAULT_PLAY_URL
    assert f"```\n{DEFAULT_PLAY_URL}\n```" in chat["markdown"]
    custom = "http://127.0.0.1:8081/novel/custom/play"
    chat_custom = format_chat_instruction(
        change="mock-change",
        assignment=_smoke_assignment(),
        play_url=custom,
    )
    assert chat_custom["play_url"] == custom
    assert f"```\n{custom}\n```" in chat_custom["markdown"]


def test_format_release_chat_message_status_labels():
    assert "PASS" in format_release_chat_message("BROWSER_CHECK_PASS")
    assert "FAIL" in format_release_chat_message("BROWSER_CHECK_FAIL")
    assert "BLOCKED" in format_release_chat_message("BROWSER_CHECK_BLOCKED")
    assert "ЗАВЕРШЕНО" in format_release_chat_message(None)


def test_write_go_ack(tmp_path: Path):
    assignment = _smoke_assignment()
    path = write_go_ack(
        tmp_path,
        required_check_id=assignment["required_check_id"],
        assignment_epoch=assignment["assignment_epoch"],
        change="mock-change",
        source="owner_chat",
    )
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["step_id"] == GO_STEP_ID
    assert is_go_acknowledged(tmp_path, assignment)


def test_pre_smoke_acknowledged_via_go_or_focus(tmp_path: Path):
    assignment = _smoke_assignment()
    assert not is_pre_smoke_acknowledged(tmp_path, assignment)

    write_go_ack(
        tmp_path,
        required_check_id=assignment["required_check_id"],
        assignment_epoch=assignment["assignment_epoch"],
    )
    assert is_pre_smoke_acknowledged(tmp_path, assignment)

    other = _smoke_assignment(assignment_epoch=4)
    assert not is_pre_smoke_acknowledged(tmp_path, other)

    write_focus_ack(
        tmp_path,
        required_check_id=other["required_check_id"],
        assignment_epoch=other["assignment_epoch"],
        change="mock-change",
    )
    assert is_pre_smoke_acknowledged(tmp_path, other)


def test_run_browser_check_release_writes_manifest(tmp_path: Path):
    assignment = _smoke_assignment()
    result = run_browser_check_release(
        repo=tmp_path,
        change="mock-change",
        assignment=assignment,
        durable_result_status="BROWSER_CHECK_PASS",
    )
    assert result["step_id"] == RELEASE_STEP_ID
    assert result["skipped"] is False
    assert "PASS" in result["chat_message"]
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["browser_action"]["tool"] == "navigate_page"
    assert "PASS" in manifest["chat_message"]
