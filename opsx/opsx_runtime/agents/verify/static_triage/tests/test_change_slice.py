"""Тесты bounded change slice."""

from __future__ import annotations

from agents.verify.static_triage.change_slice import build_change_slice


def test_change_slice_no_path():
    status, text, present = build_change_slice(
        repository_path=".",
        path=None,
        line=None,
    )
    assert status == "NO_PATH"
    assert text == ""
    assert present is False


def test_change_slice_not_in_diff_for_missing_file(tmp_path):
    status, text, present = build_change_slice(
        repository_path=str(tmp_path),
        path="does_not_exist.py",
        line=10,
    )
    assert status in ("NOT_IN_DIFF", "NO_GIT")
    assert present is False
