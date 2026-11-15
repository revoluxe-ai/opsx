"""Регрессия: BOUNDED_HUNTER — grep/glob + run_pytest, без shell."""

from __future__ import annotations

from agents.repair.bounded_hunter.agent import BoundedHunterAgent
from agents.verify.repair.run_pytest_tool import hop_includes_run_pytest_tool


def test_bounded_hunter_excludes_shell_includes_run_pytest() -> None:
    forbidden = frozenset({"shell"})
    assert forbidden.isdisjoint(BoundedHunterAgent.tools)
    assert "run_pytest" in BoundedHunterAgent.tools
    assert hop_includes_run_pytest_tool("BOUNDED_HUNTER")