"""Parent routing после implementation / materialize."""
from __future__ import annotations

from langgraph.graph import END

from agents.state import GraphState
from workflows.implementation.routing import (
    RUN_STATUS_APPLY_COMPLETE,
    _raise_if_failed_else_end_on_paused,
)

_OUTCOME_MATERIALIZE = "scenario_trace_materialize"
_OUTCOME_VERIFY = "verify"


def route_after_implementation_lane(state: GraphState) -> str:
    """APPLY_COMPLETE → materialize. PAUSED/FAILED и прочий стоп — ``raise``."""
    from workflows.implementation.routing import _raise_lane_terminal

    terminal = _raise_if_failed_else_end_on_paused(state)
    if terminal is not None:
        return terminal
    status = str(state.get("implementation_status") or "")
    if status == RUN_STATUS_APPLY_COMPLETE:
        return _OUTCOME_MATERIALIZE
    _raise_lane_terminal(state)


def route_after_scenario_trace_materialize(
    state: GraphState,
    *,
    include_verify: bool,
) -> str:
    """После materialize — verify или END (implementation-only)."""
    result = state.get("plan_artifact_materialization_result") or {}
    status = str(result.get("status") or "")
    if status not in ("MATERIALIZED", "ALREADY_CURRENT"):
        from workflows.verify.cli_outcome import VerifyImplIncompleteError

        raise VerifyImplIncompleteError(
            "scenario trace materialize did not finish: "
            f"status={(status or '(missing)')!r} result={result!r}"
        )
    if include_verify:
        return _OUTCOME_VERIFY
    return END


__all__ = [
    "route_after_implementation_lane",
    "route_after_scenario_trace_materialize",
]
