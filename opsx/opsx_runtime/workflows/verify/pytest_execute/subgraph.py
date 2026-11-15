"""Compiled graph ``pytest_execute`` (для тестов и опциональной регистрации)."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.state import GraphState

__all__ = ["build_pytest_execute_graph"]


def build_pytest_execute_graph(
    test_harness_repair_agent: Any | None = None,
) -> Any:
    """Минимальный граф: один узел-обёртка над ``run_pytest_execute_sync``."""

    async def execute_node(state: GraphState) -> dict[str, Any]:
        """Production: consumers вызывают ``run_pytest_execute_sync`` inline; граф — тестовый stub."""
        return {"pytest_execute_skipped_graph_hop": True}

    builder: StateGraph = StateGraph(GraphState)
    _ = test_harness_repair_agent
    builder.add_node("pytest_execute", execute_node)
    builder.add_edge(START, "pytest_execute")
    builder.add_edge("pytest_execute", END)
    return builder.compile()
