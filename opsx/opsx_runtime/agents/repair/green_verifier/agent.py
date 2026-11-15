"""GREEN_VERIFIER — независимая проверка RED после FIX (read + MCP run_pytest)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from agents.abc_agent import ABCAgent
from model_config.agent_roles import AgentRole
from agents.repair.green_verifier.prompt.get_prompt import PAYLOAD_MODEL
from agents.repair.helpers import GREEN_VERIFIER_TOOLS, with_receipt
from workflows.repair.checks import red_green_identity_errors
from workflows.repair.red_operative_validation import (
    red_test_file_content_identity,
    validate_bug_team_green_operative,
)
from workflows.repair.schemas import GreenEvidence, RepairState, WorkerReceipt


class GreenVerifierAgent(ABCAgent):
    """AI-исполнитель GREEN_VERIFIER для repair subgraph."""

    role = AgentRole.GREEN_VERIFIER
    payload_model = PAYLOAD_MODEL
    tools = GREEN_VERIFIER_TOOLS

    def preflight(self, state: RepairState) -> dict[str, Any] | None:
        if state.active is None or state.red is None or state.fix is None:
            return {
                "status": "BLOCKED",
                "current_stage": "finish",
                "error": "GREEN requires finding + RED + FIX evidence",
            }
        return None

    def on_success(
        self,
        state: RepairState,
        payload: BaseModel,
        receipt: WorkerReceipt,
    ) -> dict[str, Any]:
        green = GreenEvidence.model_validate(payload)
        assert state.red is not None
        operative_errors = validate_bug_team_green_operative(
            green,
            repo_root=state.repository_path,
            red_test_id=state.red.test_id,
        )
        current_identity: str | None = None
        test_id_for_identity = str(green.test_id or state.red.test_id or "")
        if test_id_for_identity and green.status == "GREEN_CONFIRMED":
            try:
                current_identity = red_test_file_content_identity(
                    Path(state.repository_path),
                    test_id_for_identity,
                )
            except (OSError, ValueError):
                operative_errors.append("GREEN_OPERATIVE_TEST_IDENTITY")
        identity_errors = [
            *operative_errors,
            *red_green_identity_errors(
                state.red,
                green,
                red_test_content_identity=state.red_test_content_identity,
                current_test_content_identity=current_identity,
            ),
        ]
        error = "; ".join(identity_errors) if identity_errors else None
        if error:
            return with_receipt(
                state,
                receipt,
                green=green,
                status="BLOCKED",
                current_stage="finish",
                error=error,
            )
        return with_receipt(
            state,
            receipt,
            green=green,
            status=green.status,
            current_stage="green",
            error=None,
        )
