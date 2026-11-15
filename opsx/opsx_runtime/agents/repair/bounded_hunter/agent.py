"""BoundedHunterAgent — поиск нарушений root invariant (роль BOUNDED_HUNTER)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agents.abc_agent import ABCAgent
from model_config.agent_roles import AgentRole
from agents.repair.bounded_hunter.prompt.get_prompt import PAYLOAD_MODEL
from agents.repair.helpers import BOUNDED_HUNTER_TOOLS, with_receipt
from workflows.repair.schemas import HuntPayload, RepairState, WorkerReceipt


class BoundedHunterAgent(ABCAgent):
    """AI-исполнитель hunt-hop (read-only repo search по инварианту)."""

    role = AgentRole.BOUNDED_HUNTER
    payload_model = PAYLOAD_MODEL
    tools = BOUNDED_HUNTER_TOOLS

    def preflight(self, state: RepairState) -> dict[str, Any] | None:
        if not state.invariants:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "hunt requires root invariant",
            }
        if state.hunt_rounds >= state.max_hunts:
            return {
                "status": "MAX_HUNTS_REACHED",
                "current_stage": "fanin",
                "error": None,
            }
        return None

    def on_success(
        self,
        state: RepairState,
        payload: BaseModel,
        receipt: WorkerReceipt,
    ) -> dict[str, Any]:
        hunt = HuntPayload.model_validate(payload)
        return with_receipt(
            state,
            receipt,
            hunts=[*state.hunts, hunt],
            hunt_rounds=state.hunt_rounds + 1,
            status=hunt.hunt_result,
            current_stage="hunt",
            error=None,
        )
