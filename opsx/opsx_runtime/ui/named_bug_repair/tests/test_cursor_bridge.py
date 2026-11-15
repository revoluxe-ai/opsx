"""Тесты sqlite bridge scope для Owner-named UI."""

from __future__ import annotations

from bootstrap import apply_named_bug_repair_process_env
from cursor_bridge import owner_named_bridge_store_scope


def test_owner_named_bridge_store_scope_slug() -> None:
    assert owner_named_bridge_store_scope("Visual Bug") == (
        "owner_named_bug_repair:visual-bug"
    )
    assert owner_named_bridge_store_scope("owner-named") == (
        "owner_named_bug_repair:owner-named"
    )


def test_bootstrap_defaults_sqlite_store(monkeypatch) -> None:
    monkeypatch.delenv("OPSX_CURSOR_SDK_STORE", raising=False)
    apply_named_bug_repair_process_env()
    assert __import__("os").environ.get("OPSX_CURSOR_SDK_STORE") == "sqlite"