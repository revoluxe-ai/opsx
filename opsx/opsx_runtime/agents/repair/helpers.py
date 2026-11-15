"""Общие хелперы для repair-исполнителей (state merge, tool sets)."""

from __future__ import annotations

from typing import Any, Mapping

from workflows.repair.schemas import RepairState

READ_ONLY = ("read", "grep", "glob", "ls")
# GREEN: read sources + один targeted pytest через MCP (без shell).
GREEN_VERIFIER_TOOLS = ("read", "run_pytest")
MUTATING = (
    "read",
    "grep",
    "glob",
    "ls",
    "edit",
)

# Repair graph (nodes_repair): без repo-wide grep/glob/ls/shell — иначе ReadScopeViolation.
CLASSIFIER_TOOLS = ("read",)
RED_TEST_WORKER_TOOLS = ("read", "edit", "run_pytest")
# Hunt-hop: поиск + один targeted pytest через MCP (§27.0.1 — не shell).
BOUNDED_HUNTER_TOOLS = ("read", "grep", "glob", "ls", "run_pytest")


def as_state(value: RepairState | Mapping[str, Any]) -> RepairState:
    """Привести raw mapping LangGraph к ``RepairState``."""

    if isinstance(value, RepairState):
        return value
    return RepairState.model_validate(value)


def with_receipt(
    state: RepairState,
    receipt,
    **updates: Any,
) -> dict[str, Any]:
    """Update LangGraph: append worker receipt и инкремент revision."""

    return {
        "runs": [*state.runs, receipt],
        "revision": state.revision + 1,
        **updates,
    }
