"""Тесты реестра детерминированных MCP-tools (allowlist, без shell)."""

from __future__ import annotations

from pathlib import Path

from agents.agent_tools.deterministic.registry import (
    RUN_PYTEST_AGENT_KEYS,
    build_deterministic_custom_tools,
    hop_includes_run_pytest_tool,
    merge_deterministic_custom_tools,
)
from agents.repair.helpers import GREEN_VERIFIER_TOOLS


def test_green_verifier_on_run_pytest_allowlist() -> None:
    assert "GREEN_VERIFIER" in RUN_PYTEST_AGENT_KEYS
    assert hop_includes_run_pytest_tool("GREEN_VERIFIER")


def test_hop_excludes_unknown_agent() -> None:
    assert not hop_includes_run_pytest_tool("CLASSIFIER")
    assert build_deterministic_custom_tools("CLASSIFIER", repo_root=Path(".")) == {}


def test_green_verifier_tools_include_run_pytest_not_shell() -> None:
    tools = set(GREEN_VERIFIER_TOOLS)
    assert "run_pytest" in tools
    assert "shell" not in tools
    assert "edit" not in tools


def test_merge_deterministic_skips_when_not_allowed(tmp_path: Path) -> None:
    payload = {"custom_tools": []}
    out = merge_deterministic_custom_tools(
        payload,
        agent_key="CLASSIFIER",
        repo_root=tmp_path,
    )
    assert out == payload


def test_merge_deterministic_adds_run_pytest_for_green(tmp_path: Path) -> None:
    out = merge_deterministic_custom_tools(
        {},
        agent_key="GREEN_VERIFIER",
        repo_root=tmp_path,
    )
    assert "custom_tools" in out
    assert "run_pytest" in out["custom_tools"]