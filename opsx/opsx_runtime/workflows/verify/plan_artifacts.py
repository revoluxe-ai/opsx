"""Read-only verify entry gate; the planning producer is a separate stage."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.common.planning_artifacts import inspect_plan_artifacts
from agents.state import GraphState
from workflows.verify.continuation import continuation_end_patch

class PlanArtifactsPreflightAgent:
    """Pure Python, read-only preflight agent: no SDK calls or mutation."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        repo = Path(str(state["repository_path"]))
        change_id = str(state.get("change_id") or "")
        result = inspect_plan_artifacts(repo, change_id)
        return {
            "plan_artifact_status": result["status"],
            "plan_artifact_result": result,
            "plan_artifact_source_digest": result.get("source_digest"),
        }


async def plan_artifacts_preflight_node(state: GraphState) -> dict[str, Any]:
    return await PlanArtifactsPreflightAgent().run(state)


def route_after_plan_artifacts_preflight(state: GraphState) -> str:
    """Verify only: never invoke writers or silently recover plan artifacts."""
    if state.get("plan_artifact_status") == "READY":
        return "verify_cycle_gate"
    return "plan_artifacts_terminal"

async def plan_artifacts_terminal_node(state: GraphState) -> dict[str, Any]:
    result = state.get("plan_artifact_result") or {}
    recovery = (
        f"{result.get('code', 'PLAN_ARTIFACTS_BLOCKED')}: "
        f"{result.get('detail', '')}; producer={result.get('producer', '')}"
    )
    return {
        "verify_stop_reason": "PLAN_ARTIFACTS_PAUSED",
        "verify_recovery_contract": recovery,
        "owner_attention": None,
        **continuation_end_patch(),
    }
