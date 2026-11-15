#!/usr/bin/env python3
"""Planning-stage LangGraph gate. No SDK, no writes, no verify-cycle shortcuts."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from langgraph.graph import START, END, StateGraph

from agents.state import GraphState
from agents.common.planning_artifacts import PlanningArtifactsPreflightAgent


async def planning_readiness_node(state: GraphState) -> dict[str, Any]:
    return await PlanningArtifactsPreflightAgent().run(state)


def build_planning_readiness_graph() -> Any:
    builder = StateGraph(GraphState)
    builder.add_node("planning_readiness", planning_readiness_node)
    builder.add_edge(START, "planning_readiness")
    builder.add_edge("planning_readiness", END)
    return builder.compile()


async def execute(change_id: str, repo_root: Path) -> dict[str, Any]:
    final = await build_planning_readiness_graph().ainvoke({
        "change_id": change_id,
        "repository_path": str(repo_root.resolve()),
    })
    return final["planning_readiness_result"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate planning artifacts before implementation")
    parser.add_argument("change_id")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    result = asyncio.run(execute(args.change_id, args.repo))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
