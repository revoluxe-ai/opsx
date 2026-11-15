"""Маршрутизация gate-repair subgraph после ``quality_gates``."""

from __future__ import annotations

from agents.state import GraphState
from workflows.verify.outer_loop import is_verify_clean, route_after_quality_gates


def _pytest_diagnostics_present(state: GraphState) -> bool:
    diag = state.get("quality_gate_diagnostics")
    if not isinstance(diag, dict):
        return False
    raw = diag.get("pytest")
    return isinstance(raw, list) and any(isinstance(x, str) and x.strip() for x in raw)


def route_after_group_error(state: GraphState) -> str:
    """Продолжать repair chain только при materialized ``analysis_result``."""
    if not isinstance(state.get("analysis_result"), dict):
        return "group_error_handoff_terminal"
    from agents.verify.environment_prep.cohort_detect import should_run_environment_prep

    if should_run_environment_prep(state):
        return "environment_prep"
    return "repair_order"


def route_after_environment_prep(state: GraphState) -> str:
    """После prep — re-gates или repair_order."""
    from agents.verify.environment_prep.cohort_detect import should_rerun_quality_gates_after_prep

    if should_rerun_quality_gates_after_prep(state):
        return "quality_gates"
    return "repair_order"


def route_after_quality_gates_with_classifier(state: GraphState) -> str:
    """FAIL с pytest diagnostics → classifier; иначе legacy routing."""
    if state.get("verify_outer_batch_final_pending"):
        return route_after_quality_gates(state)
    if is_verify_clean(state):
        return route_after_quality_gates(state)
    if str(state.get("quality_gate_verdict") or "") == "FAIL" and _pytest_diagnostics_present(state):
        return "pytest_error_classifier"
    return route_after_quality_gates(state)
