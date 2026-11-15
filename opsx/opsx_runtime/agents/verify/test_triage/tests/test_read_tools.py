"""Регрессия: test_triage — scoped read only, без repo search tools."""

from __future__ import annotations

from agents.verify.impl_verify.scoped_read_tool import hop_uses_scoped_read_tool
from agents.verify.test_triage.agent import _TEST_TRIAGE_READ_TOOLS


def test_triage_tool_list_excludes_repo_search() -> None:
    """Hop-классификатор не отдаёт grep/glob/ls — иначе ReadScopeViolation repo_root_scan."""
    forbidden = frozenset(
        {"grep", "glob", "ls", "shell", "list_dir", "listdir", "search", "edit"},
    )
    assert _TEST_TRIAGE_READ_TOOLS == ("read",)
    assert not forbidden.intersection(_TEST_TRIAGE_READ_TOOLS)
    assert hop_uses_scoped_read_tool("test_triage", _TEST_TRIAGE_READ_TOOLS)
