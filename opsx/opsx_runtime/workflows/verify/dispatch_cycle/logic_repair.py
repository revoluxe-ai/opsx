"""FIX hop bug-repair workflow для verify static LOGIC (тонкий adapter)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agents.adapters.abc_adapter import ABCAdapter
from agents.repair.fix_bug import FixBugAgent
from agents.repair.fix_bug.prompt.get_prompt import (
    build_request_payload as fix_build_payload,
    get_prompt as fix_get_prompt,
)
from agents.state import GraphState
from model_config.agent_roles import AgentRole
from workflows.repair.graph import build_repair_graph  # canonical bug-repair entrypoint
from agents.verify.repair.executor_blocked_handoff import (
    maybe_executor_blocked_product_handoff_patch,
)
from workflows.verify.dispatch_cycle.repair_state import build_verify_repair_state


def _fix_to_verify_dict(fix: Any) -> dict[str, Any] | None:
    """Привести ``FixEvidence`` из ``FixBugAgent`` к dict для commit-router и state."""

    if fix is None:
        return None
    if isinstance(fix, dict):
        return fix
    if isinstance(fix, BaseModel):
        return fix.model_dump(mode="json")
    return None


async def run_verify_logic_bug_repair(
    state: GraphState,
    adapter: ABCAdapter,
) -> dict[str, Any]:
    """Вызвать FIX hop существующего bug-repair workflow (``FixBugAgent`` / ``build_repair_graph``).

    Полный ``build_repair_graph`` (RED/GREEN/invariant) не гоняется в verify dispatch cycle —
    только IMPLEMENTATION_EXECUTOR fix hop, далее commit через ``CommitAgent``.
    """
    _ = build_repair_graph  # документируем reuse workflow package для тестов/import audit
    item = state.get("dispatcher_current_item")
    if not isinstance(item, dict):
        raise ValueError("dispatcher_current_item missing for logic_bug_repair")

    repair_state = build_verify_repair_state(state, item)
    fix_agent = FixBugAgent(adapter)
    fix_update = await fix_agent.run(
        repair_state.model_dump(mode="json"),
        prompt=fix_get_prompt(repair_state, role=AgentRole.IMPLEMENTATION_EXECUTOR),
        payload=fix_build_payload(repair_state),
    )
    merged = {**repair_state.model_dump(mode="json"), **fix_update}
    fix = _fix_to_verify_dict(merged.get("fix"))
    base_patch: dict[str, Any] = {
        "verify_finding_fix": fix,
        "verify_finding_repair_outcome": {
            "workflow": "bug_repair_fix_hop",
            "fix": fix,
        },
        "verify_static_repair_state_snapshot": {
            "mission_id": repair_state.mission_id,
            "current_stage": merged.get("current_stage"),
            "status": merged.get("status"),
        },
    }
    handoff_patch = maybe_executor_blocked_product_handoff_patch(
        {**state, **base_patch},
    )
    return {**base_patch, **handoff_patch}
