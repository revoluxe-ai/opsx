"""Регрессия: scenario_test_repair — scoped read + edit, без repo search tools."""

from __future__ import annotations

from agents.repair.scenario_test_repair.agent import _SCENARIO_TEST_REPAIR_TOOLS
from agents.verify.impl_verify.scoped_read_tool import hop_uses_scoped_read_tool


def test_scenario_test_repair_tool_list_excludes_repo_search() -> None:
    """Hop не отдаёт grep/glob/ls/shell — иначе ReadScopeViolation repo_root_scan."""
    forbidden = frozenset(
        {"grep", "glob", "ls", "shell", "list_dir", "listdir", "search"},
    )
    assert _SCENARIO_TEST_REPAIR_TOOLS == ("read", "edit")
    assert not forbidden.intersection(_SCENARIO_TEST_REPAIR_TOOLS)
    assert hop_uses_scoped_read_tool("scenario_test_repair", _SCENARIO_TEST_REPAIR_TOOLS)
