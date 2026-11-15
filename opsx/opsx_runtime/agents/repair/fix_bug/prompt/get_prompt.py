"""Промпт IMPLEMENTATION_EXECUTOR — TASK + INPUT CONTEXT; wire из ``prompt_wire``."""

from __future__ import annotations

from typing import Any

from workflows.repair.schemas import FixEvidence, RepairState
from model_config.agent_roles import AgentRole
from agents.repair.prompt_wire import build_role_prompt

PAYLOAD_MODEL = FixEvidence


def get_role_instructions() -> str:
    """ROLE-SPECIFIC TASK для FIX (только текст этой роли)."""
    return (
        "Implement the narrow root-cause fix in the mission worktree. "
        "Do not weaken or rewrite the authoritative RED expectation. "
        "Do not commit, push or deploy. Return exact changed paths. "
        "Workflow: read the failing test once; run_pytest on node_id; "
        "after RED at most one targeted read, then edit with a minimal hunk; "
        "re-run pytest on the same node_id. Never use nodeid for run_pytest."
    )


def build_request_payload(state: RepairState) -> dict[str, Any]:
    """Контекст hop (finding + classification + RED)."""
    if state.active is None or state.red is None:
        raise ValueError("FIX payload requires active finding and red evidence")
    return {
        "finding": state.active.model_dump(mode="json"),
        "classification": state.classification.model_dump(mode="json")
        if state.classification
        else None,
        "red": state.red.model_dump(mode="json"),
    }


def get_prompt(state: RepairState, role: AgentRole) -> str:
    """Полный SDK prompt для FIX hop."""
    return build_role_prompt(
        state,
        role=role,
        role_instructions=get_role_instructions(),
        payload_model=PAYLOAD_MODEL,
        context=build_request_payload(state),
    )
