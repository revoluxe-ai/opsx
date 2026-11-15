"""Справочник worker-контрактов по роли (не общие подстановки)."""

from __future__ import annotations

ROLE_WORKER_CONTRACTS: dict[str, str] = {
    "CLASSIFIER": ".cursor/pipeline/bug-team/workers/CLASSIFIER.md",
    "RED_TEST_WORKER": ".cursor/pipeline/bug-team/workers/RED_TEST_WORKER.md",
    "IMPLEMENTATION_EXECUTOR": (
        ".cursor/pipeline/bug-team/workers/IMPLEMENTATION_EXECUTOR.md"
    ),
    "GREEN_VERIFIER": ".cursor/pipeline/bug-team/workers/GREEN_VERIFIER.md",
    "INVARIANT_CAPTURE_WORKER": (
        ".cursor/pipeline/bug-team/workers/INVARIANT_CAPTURE_WORKER.md"
    ),
    "BOUNDED_HUNTER": ".cursor/pipeline/bug-team/workers/BOUNDED_HUNTER.md",
    "RELATION_CLASSIFIER": (
        ".cursor/pipeline/bug-team/workers/RELATION_CLASSIFIER.md"
    ),
    "FAN_IN_ASSEMBLER": (
        ".cursor/pipeline/bug-team/workers/FAN_IN_ASSEMBLER.md"
    ),
}

ROLE_CONTRACTS = ROLE_WORKER_CONTRACTS


def worker_contract_path(role: str) -> str:
    """Путь к worker `.md` для роли Bug Team hop."""
    try:
        return ROLE_WORKER_CONTRACTS[role]
    except KeyError as exc:
        raise KeyError(f"no worker contract mapped for role {role!r}") from exc
