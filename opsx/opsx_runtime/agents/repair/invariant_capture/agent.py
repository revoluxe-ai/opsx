"""InvariantCaptureAgent — root invariant после GREEN (роль INVARIANT_CAPTURE_WORKER)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agents.abc_agent import ABCAgent
from model_config.agent_roles import AgentRole
from agents.repair.helpers import READ_ONLY, with_receipt
from agents.repair.invariant_capture.prompt.get_prompt import PAYLOAD_MODEL
from workflows.repair.schemas import InvariantPayload, RepairState, WorkerReceipt


class InvariantCaptureAgent(ABCAgent):
    """AI-исполнитель capture-hop (роль INVARIANT_CAPTURE_WORKER)."""

    role = AgentRole.INVARIANT_CAPTURE_WORKER
    payload_model = PAYLOAD_MODEL
    tools = READ_ONLY

    def preflight(self, state: RepairState) -> dict[str, Any] | None:
        if state.invariants:
            return {
                "status": "INVARIANT_REUSED",
                "current_stage": "invariant",
            }
        if state.active is None or state.green is None:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "invariant capture requires GREEN root finding",
            }
        return None

    def on_success(
        self,
        state: RepairState,
        payload: BaseModel,
        receipt: WorkerReceipt,
    ) -> dict[str, Any]:
        invariant = InvariantPayload.model_validate(payload)
        return with_receipt(
            state,
            receipt,
            invariants=[invariant],
            status="INVARIANT_CAPTURED",
            current_stage="invariant",
            error=None,
        )
