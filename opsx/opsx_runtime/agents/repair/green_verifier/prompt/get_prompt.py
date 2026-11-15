"""Промпт GREEN_VERIFIER — TASK + INPUT CONTEXT; wire из ``prompt_wire``."""

from __future__ import annotations

from typing import Any

from workflows.repair.schemas import GreenEvidence, RepairState
from model_config.agent_roles import AgentRole
from agents.repair.prompt_wire import build_role_prompt

PAYLOAD_MODEL = GreenEvidence


def get_role_instructions() -> str:
    """ROLE-SPECIFIC TASK для GREEN (только текст этой роли)."""
    return (
        "Independently rerun the authoritative RED test and targeted "
        "regressions. Do not modify tests or production. GREEN must "
        "reuse the RED TEST_ID unless an explicit NON_WEAKEN_DELTA "
        "explains a justified test change. Do not compute file hashes. "
        "Use MCP run_pytest with the authoritative test node id only — "
        "never shell or poetry commands. Emit GREEN_CONFIRMED only after "
        "run_pytest reports exit_code=0 for that test_id."
    )


def build_request_payload(state: RepairState) -> dict[str, Any]:
    """Контекст hop (finding + RED + FIX evidence)."""
    if state.active is None or state.red is None or state.fix is None:
        raise ValueError("GREEN payload requires active finding, red, and fix")
    return {
        "finding": state.active.model_dump(mode="json"),
        "red": state.red.model_dump(mode="json"),
        "fix": state.fix.model_dump(mode="json"),
    }


def get_prompt(state: RepairState, role: AgentRole) -> str:
    """Полный SDK prompt для GREEN hop."""
    return build_role_prompt(
        state,
        role=role,
        role_instructions=get_role_instructions(),
        payload_model=PAYLOAD_MODEL,
        context=build_request_payload(state),
    )
