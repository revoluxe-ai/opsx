"""Детерминированные MCP-функции для agent hops (без shell).

Реестр capability → tool specs: ``registry``; реализация ``run_pytest`` — ``pytest_tool``.
"""

from agents.agent_tools.deterministic.registry import (
    RUN_PYTEST_AGENT_KEYS,
    build_deterministic_custom_tools,
    hop_includes_run_pytest_tool,
    merge_deterministic_custom_tools,
)

__all__ = [
    "RUN_PYTEST_AGENT_KEYS",
    "build_deterministic_custom_tools",
    "hop_includes_run_pytest_tool",
    "merge_deterministic_custom_tools",
]