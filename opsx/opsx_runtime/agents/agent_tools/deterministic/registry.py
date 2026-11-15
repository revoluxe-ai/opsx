"""Реестр детерминированных MCP-tools: discover → reuse через capability grant по agent_key."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.adapters.agent_capability_options import AgentCustomToolSpec
from agents.agent_tools.deterministic.pytest_tool import build_run_pytest_custom_tool

# Agent hops, которым runtime может выдать MCP ``run_pytest`` (без shell).
RUN_PYTEST_AGENT_KEYS: frozenset[str] = frozenset(
    {
        "test_repair",
        "IMPLEMENTATION_EXECUTOR",
        "RED_TEST_WORKER",
        "apply_red",
        "apply_green",
        "BOUNDED_HUNTER",
        "GREEN_VERIFIER",
    },
)


def hop_includes_run_pytest_tool(agent_key: str | None) -> bool:
    """True, если capability-policy разрешает MCP ``run_pytest`` для hop."""
    return str(agent_key or "").strip() in RUN_PYTEST_AGENT_KEYS


def build_deterministic_custom_tools(
    agent_key: str | None,
    *,
    repo_root: Path,
) -> Mapping[str, AgentCustomToolSpec]:
    """Собрать custom tools для hop по allowlist (без shell)."""
    if not hop_includes_run_pytest_tool(agent_key):
        return {}
    return build_run_pytest_custom_tool(repo_root=repo_root)


def merge_deterministic_custom_tools(
    local_payload: dict[str, Any],
    *,
    agent_key: str | None,
    repo_root: Path,
) -> dict[str, Any]:
    """Добавить детерминированные MCP tools в ``local.custom_tools`` для hop."""
    from agents.adapters.agent_capability_options import (
        custom_tool_specs_to_provider,
    )

    specs = dict(build_deterministic_custom_tools(agent_key, repo_root=repo_root))
    if not specs:
        return dict(local_payload)
    merged = dict(local_payload)
    merged["custom_tools"] = custom_tool_specs_to_provider(specs)
    return merged


def merge_run_pytest_custom_tools(
    local_payload: dict[str, Any],
    *,
    repo_root: Path,
    agent_key: str | None = None,
) -> dict[str, Any]:
    """Совместимость: добавить ``run_pytest`` (устаревший вызов без agent_key)."""
    key = agent_key if agent_key is not None else "__legacy_allow_run_pytest__"
    if key == "__legacy_allow_run_pytest__":
        from agents.adapters.agent_capability_options import (
            custom_tool_specs_to_provider,
        )

        specs = build_run_pytest_custom_tool(repo_root=repo_root)
        merged = dict(local_payload)
        merged["custom_tools"] = custom_tool_specs_to_provider(specs)
        return merged
    return merge_deterministic_custom_tools(
        local_payload,
        agent_key=agent_key,
        repo_root=repo_root,
    )


__all__ = [
    "RUN_PYTEST_AGENT_KEYS",
    "build_deterministic_custom_tools",
    "hop_includes_run_pytest_tool",
    "merge_deterministic_custom_tools",
    "merge_run_pytest_custom_tools",
]