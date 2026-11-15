"""Регрессия: test_repair — scoped read + edit, без repo search tools."""

from __future__ import annotations

from agents.verify.impl_verify.scoped_read_tool import hop_uses_scoped_read_tool
from agents.verify.test_repair.agent import _TEST_REPAIR_TOOLS


def test_test_repair_tool_list_excludes_repo_search() -> None:
    """Hop не отдаёт grep/glob/ls/shell — иначе ReadScopeViolation repo_root_scan."""
    forbidden = frozenset(
        {"grep", "glob", "ls", "shell", "list_dir", "listdir", "search"},
    )
    assert _TEST_REPAIR_TOOLS == ("read", "edit", "run_pytest")
    assert not forbidden.intersection(_TEST_REPAIR_TOOLS)
    assert hop_uses_scoped_read_tool("test_repair", _TEST_REPAIR_TOOLS)
