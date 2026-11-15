"""Единый перечень ролей OPSX LangGraph / verify runtime.

Строковые значения — wire-идентификаторы в receipt, prompt и ``ModelResolveRequest.role``.
"""

from __future__ import annotations

from enum import StrEnum


class AgentRole(StrEnum):
    """Все роли, для которых возможен hop или deterministic step в OPSX runtime."""

    BUG_BOT = "BUG_BOT"
    CLASSIFIER = "CLASSIFIER"
    RED_TEST_WORKER = "RED_TEST_WORKER"
    IMPLEMENTATION_EXECUTOR = "IMPLEMENTATION_EXECUTOR"
    COMMIT = "COMMIT"
    GREEN_VERIFIER = "GREEN_VERIFIER"
    INVARIANT_CAPTURE_WORKER = "INVARIANT_CAPTURE_WORKER"
    BOUNDED_HUNTER = "BOUNDED_HUNTER"
    RELATION_CLASSIFIER = "RELATION_CLASSIFIER"
    FAN_IN_ASSEMBLER = "FAN_IN_ASSEMBLER"
    FINAL_OWNER = "FINAL_OWNER"
    OWNER_GATE = "OWNER_GATE"
    MATERIALIZER = "MATERIALIZER"
    CLAIM_WORKER = "CLAIM_WORKER"
    PREFLIGHT = "PREFLIGHT"
    VERIFY_IMPL = "VERIFY_IMPL"


# Роли, попадающие в ``WorkerReceipt.role`` (repair graph + commit).
REPAIR_RECEIPT_ROLES: frozenset[AgentRole] = frozenset(
    {
        AgentRole.CLASSIFIER,
        AgentRole.RED_TEST_WORKER,
        AgentRole.IMPLEMENTATION_EXECUTOR,
        AgentRole.COMMIT,
        AgentRole.GREEN_VERIFIER,
        AgentRole.INVARIANT_CAPTURE_WORKER,
        AgentRole.BOUNDED_HUNTER,
        AgentRole.RELATION_CLASSIFIER,
        AgentRole.FAN_IN_ASSEMBLER,
    }
)


def coerce_agent_role(value: str | AgentRole) -> AgentRole:
    """Нормализовать wire-строку или enum для сравнения и resolve."""
    if isinstance(value, AgentRole):
        return value
    return AgentRole(value)
