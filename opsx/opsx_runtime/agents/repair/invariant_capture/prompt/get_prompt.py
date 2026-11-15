"""Промпт INVARIANT_CAPTURE_WORKER — TASK + INPUT CONTEXT; wire из ``prompt_wire``."""

from __future__ import annotations

from typing import Any

from workflows.repair.schemas import InvariantPayload, RepairState
from model_config.agent_roles import AgentRole
from agents.repair.prompt_wire import build_role_prompt

PAYLOAD_MODEL = InvariantPayload


def get_role_instructions() -> str:
    """ROLE-SPECIFIC TASK для capture invariant (только текст этой роли)."""
    return (
        "Derive one precise root invariant from the proven RED->FIX->GREEN "
        "episode. It must be specific enough to drive an ecosystem hunt. "
        "Do not invent a vague 'should work correctly' invariant."
    )


def build_request_payload(state: RepairState) -> dict[str, Any]:
    """Контекст hop (finding + RED/FIX/GREEN evidence)."""
    if state.active is None or state.green is None:
        raise ValueError(
            "INVARIANT payload requires active finding and green evidence"
        )
    return {
        "finding": state.active.model_dump(mode="json"),
        "classification": state.classification.model_dump(mode="json")
        if state.classification
        else None,
        "red": state.red.model_dump(mode="json") if state.red else None,
        "fix": state.fix.model_dump(mode="json") if state.fix else None,
        "green": state.green.model_dump(mode="json"),
    }


def get_prompt(state: RepairState, role: AgentRole) -> str:
    """Полный SDK prompt для INVARIANT hop."""
    return build_role_prompt(
        state,
        role=role,
        role_instructions=get_role_instructions(),
        payload_model=PAYLOAD_MODEL,
        context=build_request_payload(state),
    )
