"""Регрессия: IMPLEMENTATION_EXECUTOR — scoped read + edit, без repo search tools."""

from __future__ import annotations

from agents.repair.fix_bug.agent import _IMPLEMENTATION_EXECUTOR_TOOLS
from agents.repair.fix_bug.prompt.get_prompt import get_role_instructions
from agents.verify.impl_verify.scoped_read_tool import hop_uses_scoped_read_tool
from agents.verify.repair.run_pytest_tool import hop_includes_run_pytest_tool


def test_implementation_executor_tool_list_excludes_repo_search() -> None:
    """Hop не отдаёт grep/glob/ls/shell — иначе ReadScopeViolation repo_root_scan."""
    forbidden = frozenset(
        {"grep", "glob", "ls", "shell", "list_dir", "listdir", "search"},
    )
    assert _IMPLEMENTATION_EXECUTOR_TOOLS == ("read", "edit", "run_pytest")
    assert not forbidden.intersection(_IMPLEMENTATION_EXECUTOR_TOOLS)
    assert hop_uses_scoped_read_tool(
        "IMPLEMENTATION_EXECUTOR",
        _IMPLEMENTATION_EXECUTOR_TOOLS,
    )
    assert hop_includes_run_pytest_tool("IMPLEMENTATION_EXECUTOR")


def test_implementation_executor_prompt_requires_pytest_edit_cycle() -> None:
    """Регрессия: hop должен требовать node_id pytest → edit → re-run, не open-ended hunt."""
    block = get_role_instructions()
    assert "run_pytest on node_id" in block
    assert "minimal hunk" in block
    assert "Never use nodeid" in block
