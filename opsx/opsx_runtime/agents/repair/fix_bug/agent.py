"""FixBugAgent — узкий fix в isolated workspace (роль IMPLEMENTATION_EXECUTOR)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agents.abc_agent import ABCAgent
from model_config.agent_roles import AgentRole
from agents.repair.fix_bug.prompt.get_prompt import PAYLOAD_MODEL
from agents.repair.helpers import with_receipt
from workflows.repair.schemas import FixEvidence, RepairState, WorkerReceipt

# IMPLEMENTATION_EXECUTOR: scoped read + edit + MCP run_pytest; без grep/glob/ls/shell.
_IMPLEMENTATION_EXECUTOR_TOOLS = (
    "read",
    "edit",
    "run_pytest",
)


class FixBugAgent(ABCAgent):
    """AI-исполнитель fix-hop (роль IMPLEMENTATION_EXECUTOR)."""

    role = AgentRole.IMPLEMENTATION_EXECUTOR
    payload_model = PAYLOAD_MODEL
    tools = _IMPLEMENTATION_EXECUTOR_TOOLS

    def preflight(self, state: RepairState) -> dict[str, Any] | None:
        if state.active is None or state.red is None:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "implementation requires active finding + RED",
            }
        if state.red.status != "RED_CONFIRMED":
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": f"implementation forbidden after {state.red.status}",
            }
        if state.owner_go is not True:
            return {
                "status": "WAITING_OWNER",
                "current_stage": "owner_gate",
                "error": "production/local implementation GO not granted",
            }
        return None

    def journal_observations(self, state, payload, receipt, *, failed: bool = False):
        """HOP_RESULT fix для активного finding."""
        from bug_log.schemas import BugObservation, Provenance

        if failed or state.active is None or not state.active.bug_id or payload is None:
            return []
        fix = FixEvidence.model_validate(payload)
        return [
            BugObservation(
                bug_id=state.active.bug_id,
                summary=fix.summary or fix.status,
                status="FIXING" if fix.status != "FIXED" else "GREEN_CONFIRMED",
                provenance=Provenance(
                    agent_role="IMPLEMENTATION_EXECUTOR",
                    hop="fix",
                    lane="fix",
                ),
                detail={"files_changed": list(fix.files_changed or [])},
            )
        ]

    def on_success(
        self,
        state: RepairState,
        payload: BaseModel,
        receipt: WorkerReceipt,
    ) -> dict[str, Any]:
        fix = FixEvidence.model_validate(payload)
        from agents.verify.repair.hop_cannot_complete import raise_if_repair_hop_cannot_complete

        finding_id = ""
        if state.active is not None:
            finding_id = str(state.active.id or "")
        raise_if_repair_hop_cannot_complete(
            hop="IMPLEMENTATION_EXECUTOR",
            finding_id=finding_id,
            status=fix.status,
            summary=fix.summary,
        )
        return with_receipt(
            state,
            receipt,
            fix=fix,
            status=fix.status,
            current_stage="fix",
            error=None if fix.status == "FIXED" else fix.notes,
        )
