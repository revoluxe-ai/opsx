"""Промпт BOUNDED_HUNTER — TASK + INPUT CONTEXT; wire из ``prompt_wire``."""

from __future__ import annotations

from typing import Any

from workflows.repair.schemas import HuntPayload, RepairState
from model_config.agent_roles import AgentRole
from agents.repair.prompt_wire import build_role_prompt

PAYLOAD_MODEL = HuntPayload


def get_role_instructions() -> str:
    """ROLE-SPECIFIC TASK для bounded invariant hunt."""
    return (
        "Search the repository for violations of the root invariant. "
        "Do not fix code, write RED tests, choose final routes or close "
        "candidates. Return CLEAR, CANDIDATES_FOUND or INCONCLUSIVE "
        "with evidence-backed candidate objects. Do not re-emit an "
        "already classified candidate ID unless new evidence changes "
        "its identity or relation. "
        "To run pytest use the run_pytest tool with a single tests/ node_id — "
        "never shell or poetry commands."
    )


def build_request_payload(state: RepairState) -> dict[str, Any]:
    """Контекст hop (invariant, finished findings, prior relations)."""
    if not state.invariants:
        raise ValueError("BOUNDED_HUNTER payload requires root invariant")
    return {
        "root_invariant": state.invariants[0].model_dump(mode="json"),
        "finished_findings": [
            outcome.model_dump(mode="json") for outcome in state.finished
        ],
        "seen_finding_ids": state.seen,
        "previous_relations": [
            relation.model_dump(mode="json") for relation in state.relations
        ],
        "hunt_round": state.hunt_rounds + 1,
        "max_hunts": state.max_hunts,
    }


def get_prompt(state: RepairState, role: AgentRole) -> str:
    """Полный SDK prompt для BOUNDED_HUNTER hop."""
    return build_role_prompt(
        state,
        role=role,
        role_instructions=get_role_instructions(),
        payload_model=PAYLOAD_MODEL,
        context=build_request_payload(state),
    )
