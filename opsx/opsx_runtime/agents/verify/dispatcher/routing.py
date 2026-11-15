"""Маршрутизация finding в static vs test workflow subgraph."""

from __future__ import annotations

from typing import Any, Literal

DispatchRoute = Literal["static", "test", "unknown"]

_STATIC_PREFIXES = (
    "WF_PYRIGHT_",
    "WF_MYPY_",
)
_TEST_PREFIXES = ("WF_PYTEST_",)


def classify_dispatch_route(item: dict[str, Any]) -> DispatchRoute:
    """Классифицировать элемент очереди для выбора subgraph."""
    workflow = str(item.get("repair_workflow") or "")
    source_tool = item.get("source_tool")
    if source_tool == "pytest" or workflow.startswith(_TEST_PREFIXES):
        return "test"
    if source_tool in ("pyright", "mypy") or any(
        workflow.startswith(p) for p in _STATIC_PREFIXES
    ):
        return "static"
    if workflow == "WF_UNKNOWN_GATE":
        return "unknown"
    lower = str(item.get("observation") or "").lower()
    if "pytest" in lower or "test_" in lower:
        return "test"
    return "static"
