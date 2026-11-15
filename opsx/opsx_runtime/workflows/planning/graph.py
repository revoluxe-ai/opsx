"""Planning subgraph: scenario-coverage plan перед implementation."""
from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.planning.scenario_coverage_plan.agent import ScenarioCoveragePlanAgent
from agents.state import GraphState

_OUTCOME_IMPLEMENTATION = "implementation"


def route_after_planning(state: GraphState) -> str:
    """READY → implementation. Иной статус — ``raise``, не тихий ``END``."""
    from workflows.verify.cli_outcome import VerifyImplIncompleteError

    status = str(state.get("planning_readiness_status") or "")
    if status == "READY":
        return _OUTCOME_IMPLEMENTATION
    result = state.get("planning_readiness_result")
    detail = result if isinstance(result, dict) else {"status": status or "(missing)"}
    raise VerifyImplIncompleteError(
        "planning stopped before implementation: "
        f"planning_readiness_status={(status or '(missing)')!r} "
        f"result={detail!r}"
    )


async def scenario_coverage_plan_node(
    agent: ScenarioCoveragePlanAgent,
    state: GraphState,
) -> dict[str, Any]:
    """Обёртка planning-агента для LangGraph."""
    return await agent.run(state)


def build_planning_graph(
    scenario_coverage_plan: ScenarioCoveragePlanAgent,
    *,
    checkpointer=None,
):
    """``START → scenario_coverage_plan → END`` (parent роутит дальше)."""
    builder = StateGraph(GraphState)
    handler = partial(scenario_coverage_plan_node, scenario_coverage_plan)
    _ = builder.add_node("scenario_coverage_plan", handler)
    _ = builder.add_edge(START, "scenario_coverage_plan")
    _ = builder.add_edge("scenario_coverage_plan", END)
    return builder.compile(checkpointer=checkpointer)


__all__ = ["build_planning_graph", "route_after_planning", "scenario_coverage_plan_node"]
