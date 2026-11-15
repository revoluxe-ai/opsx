"""Регрессия: project_bug_hunt — только scoped read, без repo search tools."""

from __future__ import annotations

from agents.verify.project_bug_hunt.agent import _PROJECT_BUG_HUNT_TOOLS


def test_project_bug_hunt_tool_list_excludes_repo_search() -> None:
    """Hop не отдаёт grep/glob/ls/shell — иначе ReadScopeViolation repo_root_scan."""
    forbidden = frozenset(
        {"grep", "glob", "ls", "shell", "list_dir", "listdir", "search"},
    )
    assert _PROJECT_BUG_HUNT_TOOLS == ("read",)
    assert not forbidden.intersection(_PROJECT_BUG_HUNT_TOOLS)
