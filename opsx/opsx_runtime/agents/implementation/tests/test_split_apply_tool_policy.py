"""Регрессия: apply_red / apply_green — scoped tools без repo search shell."""

from __future__ import annotations

from agents.implementation.apply_green.agent import _APPLY_GREEN_TOOLS
from agents.implementation.apply_red.agent import _APPLY_RED_TOOLS
from agents.verify.impl_verify.scoped_read_tool import hop_uses_scoped_read_tool
from agents.verify.repair.run_pytest_tool import hop_includes_run_pytest_tool


def test_apply_red_tool_list_excludes_repo_search() -> None:
    """Без grep/glob/ls/shell — иначе ReadScopeViolation repo_root_scan."""
    forbidden = frozenset({"grep", "glob", "ls", "shell"})
    assert _APPLY_RED_TOOLS == ("read", "edit", "run_pytest")
    assert not forbidden.intersection(_APPLY_RED_TOOLS)
    assert hop_includes_run_pytest_tool("apply_red")
    assert hop_uses_scoped_read_tool("apply_red", _APPLY_RED_TOOLS)


def test_apply_green_tool_list_excludes_repo_search() -> None:
    forbidden = frozenset({"grep", "glob", "ls", "shell"})
    assert _APPLY_GREEN_TOOLS == ("read", "edit", "run_pytest")
    assert not forbidden.intersection(_APPLY_GREEN_TOOLS)
    assert hop_includes_run_pytest_tool("apply_green")
    assert hop_uses_scoped_read_tool("apply_green", _APPLY_GREEN_TOOLS)
