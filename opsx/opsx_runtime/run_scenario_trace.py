#!/usr/bin/env python3
"""Separate LangGraph test-authoring handoff; never invoked from verify.

State machine: START → scenario_trace_materialize → END.
The only permitted write is the atomic test_trace_matrix.json replacement.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from agents.state import GraphState
from agents.common.planning_artifacts import inspect_plan_artifacts
from agents.implementation.scenario_trace_materializer.agent import scenario_trace_materialize_node


def build_test_trace_graph() -> Any:
    builder = StateGraph(GraphState)
    builder.add_node("scenario_trace_materialize", scenario_trace_materialize_node)
    builder.add_edge(START, "scenario_trace_materialize")
    builder.add_edge("scenario_trace_materialize", END)
    return builder.compile()


async def execute(change_id: str, repo_root: Path) -> dict[str, Any]:
    root = repo_root.resolve()
    state: GraphState = {"change_id": change_id, "repository_path": str(root)}
    final = await build_test_trace_graph().ainvoke(state)
    materialized = final.get("plan_artifact_materialization_result") or {}
    ready = inspect_plan_artifacts(root, change_id, phase="verify")
    if materialized.get("status") in ("MATERIALIZED", "ALREADY_CURRENT") and ready["status"] == "READY":
        return {
            "status": "TRACE_READY_FOR_AUDIT",
            "producer": materialized,
            "preflight": ready,
            "next": "/opsx-spec-scenario-coverage mode=audit change=" + change_id,
        }
    return {
        "status": "PAUSE",
        "producer": materialized,
        "preflight": ready,
        "next": materialized.get("handoff") or ready.get("producer") or "OWNER_ATTENTION",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Test-authorship trace materialization")
    parser.add_argument("change_id")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = asyncio.run(execute(args.change_id, args.repo))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "TRACE_READY_FOR_AUDIT" else 2


if __name__ == "__main__":
    raise SystemExit(main())
