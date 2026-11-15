"""Тесты process env для Owner-named bug repair UI."""

from __future__ import annotations

import os

from bootstrap import apply_named_bug_repair_process_env


def test_apply_named_bug_repair_process_env_sets_defaults(monkeypatch) -> None:
    monkeypatch.delenv("CURSOR_USE_HTTP1", raising=False)
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)
    apply_named_bug_repair_process_env()
    assert os.environ.get("CURSOR_USE_HTTP1") == "1"
    assert os.environ.get("PYTHONUNBUFFERED") == "1"
    assert os.environ.get("OPSX_CURSOR_SDK_STORE") == "sqlite"
    apply_named_bug_repair_process_env()
    assert os.environ.get("CURSOR_USE_HTTP1") == "1"