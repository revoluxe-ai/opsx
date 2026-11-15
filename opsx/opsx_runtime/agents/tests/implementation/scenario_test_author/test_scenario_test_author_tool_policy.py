"""Регрессия: scenario_test_author — scoped read + edit, без repo search tools."""

from __future__ import annotations

from agents.implementation.scenario_test_author.agent import _SCENARIO_TEST_AUTHOR_TOOLS
from agents.verify.impl_verify.scoped_read_tool import hop_uses_scoped_read_tool

_SCENARIO_TEST_AUTHOR_NODE = "scenario_test_author"


def test_scenario_test_author_tool_list_excludes_repo_search() -> None:
    """Hop ``scenario_test_author`` не отдаёт grep/glob/ls/shell — иначе repo_root_scan."""
    forbidden = frozenset(
        {"grep", "glob", "ls", "shell", "list_dir", "listdir", "search"},
    )
    assert _SCENARIO_TEST_AUTHOR_TOOLS == ("read", "edit")
    assert not forbidden.intersection(_SCENARIO_TEST_AUTHOR_TOOLS)
    assert hop_uses_scoped_read_tool(
        _SCENARIO_TEST_AUTHOR_NODE,
        _SCENARIO_TEST_AUTHOR_TOOLS,
    )
