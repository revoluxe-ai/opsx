"""Классификация findings для выбора downstream repair workflow."""

from __future__ import annotations

from typing import Literal

from agents.verify.group_error.schemas import GateToolId, RepairWorkflowKind
from agents.verify.pytest_run_health import is_pytest_harness_observation

__all__ = ["RepairWorkflowKind", "classify_repair_workflow"]


def classify_repair_workflow(
    *,
    source_tool: GateToolId | None,
    observation: str,
) -> RepairWorkflowKind:
    """Определить repair workflow по инструменту и тексту диагностики (детерминированно).

    Args:
        source_tool: pytest / pyright / mypy.
        observation: строка issue из quality_gate_diagnostics.

    Returns:
        Slug workflow для последующего repair pipeline.
    """
    lower = observation.lower()
    if source_tool == "pytest":
        if is_pytest_harness_observation(observation):
            return "WF_PYTEST_COLLECTION"
        return "WF_PYTEST_FAILURE"
    if source_tool == "pyright":
        if "reportunusedcallresult" in lower or "reportgeneral" in lower:
            return "WF_PYRIGHT_LINT"
        return "WF_PYRIGHT_TYPE"
    if source_tool == "mypy":
        if "import-untyped" in lower or "import-not-found" in lower:
            return "WF_MYPY_IMPORT"
        if "name-defined" in lower or "attr-defined" in lower or "call-arg" in lower:
            return "WF_MYPY_CONTRACT"
        return "WF_MYPY_TYPE"
    return "WF_UNKNOWN_GATE"
