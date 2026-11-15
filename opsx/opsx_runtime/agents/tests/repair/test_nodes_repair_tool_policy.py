"""Регрессия: CLASSIFIER / RED_TEST_WORKER в repair graph — scoped tools."""

from __future__ import annotations

from agents.repair.helpers import CLASSIFIER_TOOLS, RED_TEST_WORKER_TOOLS
from agents.verify.impl_verify.scoped_read_tool import hop_uses_scoped_read_tool
from agents.verify.repair.run_pytest_tool import hop_includes_run_pytest_tool


def test_classifier_tool_list_excludes_repo_search() -> None:
    forbidden = frozenset({"grep", "glob", "ls", "shell"})
    assert CLASSIFIER_TOOLS == ("read",)
    assert not forbidden.intersection(CLASSIFIER_TOOLS)
    assert hop_uses_scoped_read_tool("CLASSIFIER", CLASSIFIER_TOOLS)


def test_red_test_worker_tool_list_matches_fix_bug_pattern() -> None:
    forbidden = frozenset({"grep", "glob", "ls", "shell"})
    assert RED_TEST_WORKER_TOOLS == ("read", "edit", "run_pytest")
    assert not forbidden.intersection(RED_TEST_WORKER_TOOLS)
    assert hop_uses_scoped_read_tool("RED_TEST_WORKER", RED_TEST_WORKER_TOOLS)
    assert hop_includes_run_pytest_tool("RED_TEST_WORKER")
