"""LangGraph orchestration of the real OPSX verification contract.

Policy stays in tools/temporal/verification_transition_policy.py for now.
Temporal Server is never accessed; shared modules are pure Python policies.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from .journal import DispatchJournal

_TEMPORAL = Path(__file__).resolve().parents[1] / "temporal"
if str(_TEMPORAL) not in sys.path:
    sys.path.insert(0, str(_TEMPORAL))

from verification_engine import (  # noqa: E402
    TERMINAL_SUCCESS,
    validate_assignment_for_execute,
)
from verification_transition_policy import (  # noqa: E402
    TransitionTracker,
    evaluate_after_assigned_check,
    evaluate_after_next_check,
    evaluate_before_executor,
)


class VerificationBackend(Protocol):
    def ensure_context(self, change: str) -> Mapping[str, Any]: ...
    def next_check(self, change: str) -> Mapping[str, Any]: ...
    def execute_assigned(
        self, change: str, assignment: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...
    def inspect_result(self, assignment: Mapping[str, Any]) -> dict | None: ...
    def recovered_result(self, assignment: Mapping[str, Any], durable: Mapping[str, Any]) -> dict: ...


class VerificationState(TypedDict, total=False):
    provider: str
    model_id: str
    run_id: str
    change: str
    status: str
    route: str
    terminal: dict[str, Any]
    context: dict[str, Any]
    next_check_runs: int
    executor_runs: int
    last_next: dict[str, Any]
    last_assigned: dict[str, Any]
    assignment: dict[str, Any]
    tracker: dict[str, Any]


def _tracker_from(data: Mapping[str, Any]):
    return TransitionTracker(
        last_progress_key=data.get("last_progress_key"),
        last_post_executor_signature=data.get("last_post_executor_signature"),
        failure_causes=tuple(data.get("failure_causes") or ()),
    )


def _tracker_to(obj: TransitionTracker):
    return {
        "last_progress_key": obj.last_progress_key,
        "last_post_executor_signature": obj.last_post_executor_signature,
        "failure_causes": list(obj.failure_causes),
    }


def _stop(state: VerificationState, status: str, *, extra: dict | None = None):
    return {
        "route": "stop",
        "status": status,
        "terminal": {
            "status": status,
            "change": state["change"],
            "next_check_runs": state.get("next_check_runs", 0),
            "executor_runs": state.get("executor_runs", 0),
            "last_next_check": state.get("last_next"),
            "last_assigned_check": state.get("last_assigned"),
            **(extra or {}),
        },
    }


def build_verification_graph(
    backend: VerificationBackend,
    journal: DispatchJournal,
    *,
    repo: str | Path,
    checkpointer: Any,
    max_executor_runs: int = 64,
    next_check_technical_retries: int = 2,
):
    """Actual flow: preflight -> next_check -> policy -> dispatch -> reconcile."""
    if max_executor_runs <= 0 or next_check_technical_retries < 0:
        raise ValueError("Invalid verification budgets")
    repo = str(Path(repo).resolve())

    def preflight(state: VerificationState):
        if not state.get("run_id") or not state.get("change"):
            return _stop(state, "CONTRACT_ERROR", extra={"block_reason": "missing identity"})
        ctx = dict(backend.ensure_context(state["change"]))
        if ctx.get("status") not in (None, "OK", "READY"):
            return _stop(state, str(ctx.get("status") or "BLOCKED"), extra=ctx)
        return {
            "context": ctx,
            "status": "READY",
            "route": "continue",
            "next_check_runs": 0,
            "executor_runs": 0,
            "tracker": _tracker_to(TransitionTracker()),
        }

    def next_check(state: VerificationState):
        last = {}
        for _ in range(next_check_technical_retries + 1):
            last = dict(backend.next_check(state["change"]))
            if last.get("completed") or last.get("status") != "NEXT_CHECK_TECHNICAL_FAILURE":
                break
        return {
            "last_next": last,
            "next_check_runs": state.get("next_check_runs", 0) + 1,
            "status": str(last.get("status") or "BLOCKED"),
        }

    def decide(state: VerificationState):
        check = state["last_next"]
        if not check.get("completed"):
            return _stop(state, "NEXT_CHECK_TECHNICAL_FAILURE")
        if check.get("status") == TERMINAL_SUCCESS:
            return _stop(
                state, TERMINAL_SUCCESS,
                extra={
                    "progress_pass": check.get("progress_pass"),
                    "progress_total": check.get("progress_total"),
                    "ledger_path": check.get("ledger_path"),
                    "next_action": f"/opsx-verify change={state['change']} mode=archive",
                },
            )

        assigned = state.get("last_assigned")
        terminal = evaluate_after_next_check(
            check,
            repo=repo,
            last_executed_id=assigned.get("required_check_id") if assigned else None,
            next_check_runs=state["next_check_runs"],
            executor_runs=state.get("executor_runs", 0),
            last_next=check,
            last_assigned=assigned,
        )
        if terminal is not None:
            return _stop(state, str(terminal["status"]), extra=terminal)

        assignment = check.get("execute_assignment")
        if check.get("status") != "CHECK_REQUIRED" or not isinstance(assignment, dict):
            return _stop(
                state,
                str(check.get("status") or "CONTRACT_ERROR")
                if check.get("status") != "CHECK_REQUIRED" else "CONTRACT_ERROR",
                extra={"block_reason": "no_executable_assignment"},
            )

        if state.get("executor_runs", 0) >= max_executor_runs:
            return _stop(state, "MAX_EXECUTOR_RUNS_REACHED")

        tracker = _tracker_from(state.get("tracker") or {})
        stalled = evaluate_before_executor(
            tracker,
            check,
            executor_runs=state.get("executor_runs", 0),
            next_check_runs=state["next_check_runs"],
            last_next=check,
            last_assigned=assigned,
        )
        if stalled is not None:
            return _stop(state, str(stalled["status"]), extra=stalled)
        missing = validate_assignment_for_execute(assignment)
        if missing:
            return _stop(
                state, "VERIFICATION_CHECK_BLOCKED",
                extra={"block_reason": "ASSIGNMENT_INCOMPLETE", "missing_fields": missing},
            )
        return {"route": "dispatch", "assignment": assignment}

    def dispatch(state: VerificationState):
        outcome = journal.execute(
            backend, state["run_id"], state["change"], state["assignment"]
        )
        return {
            "last_assigned": outcome,
            "executor_runs": state.get("executor_runs", 0) + 1,
        }

    def reconcile(state: VerificationState):
        tracker = _tracker_from(state.get("tracker") or {})
        terminal = evaluate_after_assigned_check(
            tracker,
            state["last_next"],
            state["last_assigned"],
            next_check_runs=state["next_check_runs"],
            executor_runs=state["executor_runs"],
            last_next=state["last_next"],
        )
        updated = {"tracker": _tracker_to(tracker)}
        if terminal is not None:
            updated.update(_stop(state, str(terminal["status"]), extra=terminal))
        else:
            updated.update(status="EXECUTOR_RECONCILED", route="continue")
        return updated

    g = StateGraph(VerificationState)
    g.add_node("preflight", preflight)
    g.add_node("next_check", next_check)
    g.add_node("decide", decide)
    g.add_node("dispatch", dispatch)
    g.add_node("reconcile", reconcile)
    g.add_edge(START, "preflight")
    g.add_conditional_edges(
        "preflight", lambda s: "next_check" if s["route"] == "continue" else END
    )
    g.add_edge("next_check", "decide")
    g.add_conditional_edges(
        "decide", lambda s: "dispatch" if s["route"] == "dispatch" else END
    )
    g.add_edge("dispatch", "reconcile")
    g.add_conditional_edges(
        "reconcile", lambda s: "next_check" if s["route"] == "continue" else END
    )
    return g.compile(checkpointer=checkpointer)
