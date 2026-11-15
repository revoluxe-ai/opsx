"""Parent-only узлы scenario authoring и trace materialize."""
from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Any

from agents.implementation.scenario_test_author.agent import ScenarioTestAuthorAgent
from agents.implementation.scenario_trace_materializer.agent import scenario_trace_materialize_node
from agents.state import GraphState
from workflows.implementation.routing import ImplementationLaneTerminalError
from workflows.repo_paths import resolve_opsx_repository_root


async def parent_scenario_test_authoring_node(
    agent: ScenarioTestAuthorAgent,
    state: GraphState,
) -> dict[str, Any]:
    """Authoring после apply-design; raise при incomplete authoring."""
    patch = await agent.run(state)
    status = str(patch.get("scenario_authoring_status") or "")
    if status == "COMPLETE":
        return patch
    result = patch.get("scenario_authoring_result") or {}
    recovery = (
        f"{result.get('status', 'BLOCKED')}: pending={result.get('pending_after', [])}; "
        f"{result.get('block_reason') or ''}"
    )
    change_id = str(state.get("change_id") or "")
    raise ImplementationLaneTerminalError(
        f"scenario_test_authoring blocked: change_id={change_id!r} recovery={recovery!r}",
    )


async def parent_scenario_trace_materialize_node(state: GraphState) -> dict[str, Any]:
    """Materialize на parent; raise при блокировке."""
    patch = await scenario_trace_materialize_node(state)
    result = patch.get("plan_artifact_materialization_result") or {}
    status = str(result.get("status") or "")
    if status in ("MATERIALIZED", "ALREADY_CURRENT"):
        return patch
    parts: list[str] = []
    reason = str(result.get("reason") or "").strip()
    if reason:
        parts.append(reason)
    unresolved = result.get("unresolved")
    if isinstance(unresolved, list) and unresolved:
        parts.append(f"unresolved={','.join(str(x) for x in unresolved)}")
    recovery = "; ".join(parts) if parts else "TRACE_MATERIALIZATION_BLOCKED"
    change_id = str(state.get("change_id") or "")
    _ = resolve_opsx_repository_root(Path(str(state["repository_path"])))
    raise ImplementationLaneTerminalError(
        f"scenario_trace_materialize blocked: change_id={change_id!r} "
        f"materialization_status={status!r} recovery={recovery!r}",
    )


def authoring_node_factory(agent: ScenarioTestAuthorAgent):
    """Собрать partial handler authoring с зафиксированным агентом."""

    async def _node(state: GraphState) -> dict[str, Any]:
        return await parent_scenario_test_authoring_node(agent, state)

    return _node
