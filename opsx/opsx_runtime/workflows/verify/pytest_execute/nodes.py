"""DET-хелперы workflow ``pytest_execute``."""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.pytest_run_health import CollectHealthResult, affected_test_paths


def build_harness_repair_item(
    state: GraphState,
    collect: CollectHealthResult,
    modules: list[str],
) -> dict[str, Any]:
    """Synthetic dispatch item для harness repair hop."""
    diag = state.get("quality_gate_diagnostics")
    extra: list[str] = []
    if isinstance(diag, dict) and isinstance(diag.get("pytest"), list):
        for line in diag["pytest"][:20]:
            if isinstance(line, str):
                extra.extend(affected_test_paths(line))
    paths = sorted(set(modules) | set(extra))
    primary = paths[0] if paths else "tests"
    return {
        "finding_id": "HARNESS-EXEC",
        "observation": collect.log_tail[:4000],
        "path": primary,
        "source_tool": "pytest",
        "affected_modules": paths,
        "group_finding_ids": ["HARNESS-EXEC"],
        "repair_workflow": "WF_PYTEST_COLLECTION",
    }
