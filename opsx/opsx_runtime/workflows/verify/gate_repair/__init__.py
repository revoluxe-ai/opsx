"""Gate-repair LangGraph (triage chain + dispatch cycle)."""

from __future__ import annotations

from typing import Any

__all__ = ["build_gate_repair_graph", "build_gate_repair_subgraph"]


def __getattr__(name: str) -> Any:
    """Ленивый экспорт subgraph-builder — не тянуть agents при import static_scope."""
    if name in ("build_gate_repair_graph", "build_gate_repair_subgraph"):
        from workflows.verify.gate_repair.subgraph import (
            build_gate_repair_graph,
            build_gate_repair_subgraph,
        )

        return build_gate_repair_graph if name == "build_gate_repair_graph" else build_gate_repair_subgraph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
