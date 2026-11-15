"""Deterministic mirror of Stroy PBH repair materialize gate (port-branch).

Donor Game workflow at e07907b uses a simpler repair branch in ``workflow.py``
(no ``classify_completed_repair``). This module preserves Stroy integration
semantics for PBH tests and control-plane without extending donor ``models.py``.
"""

from __future__ import annotations

from typing import Any

REPAIR_BLOCKED = "REPAIR_BLOCKED"

_ORCHESTRATION_BLOCKING_REPAIR_TERMINALS = frozenset(
    {
        "OWNER_ADJUDICATION_REQUIRED",
        "PAUSE",
    }
)

_REPAIR_MATERIALIZE_SUCCESS_STATUSES = frozenset(
    {
        "BUG_BATCH_CLEAR",
        "SECURITY_BATCH_CLEAR",
    }
)


def is_repair_orchestration_blocking_terminal(status: str | None) -> bool:
    """Workflow-safe: blocking orch STATUS (no I/O)."""
    token = (status or "").strip().upper()
    if not token:
        return False
    if token.startswith("BLOCKED_") or token.startswith("PAUSE_"):
        return True
    if token == "FAIL_CLOSED_RECURRING_CAUSE":
        return True
    return token in _ORCHESTRATION_BLOCKING_REPAIR_TERMINALS


def repair_authorizes_materialize(
    *,
    terminal_status: str | None,
    repaired_this_batch: bool | None,
    bug_batch_clear: bool | None,
) -> bool:
    """True only when commit-all / re-hunt is authorized."""
    token = (terminal_status or "").strip().upper()
    if is_repair_orchestration_blocking_terminal(token):
        return False
    if token in {"PARTIAL_COMPLETE", "FAIL_CLOSED"}:
        return False
    if repaired_this_batch is False:
        return False
    if token in _REPAIR_MATERIALIZE_SUCCESS_STATUSES:
        return True
    return bug_batch_clear is True


def _orch_blocking_recovery_message(token: str) -> str:
    return (
        f"Repair orch reported blocking terminal STATUS: {token}; "
        "NEXT_ACTION: resolve via native OPSX orch route; "
        "stopping before commit materialize or re-hunt."
    )


def classify_completed_repair(
    *,
    terminal_status: str | None,
    repaired_this_batch: bool | None,
    bug_batch_clear: bool | None = None,
) -> tuple[str, str] | None:
    """Map a completed repair hop to a loop terminal, or None to allow materialize."""
    token = (terminal_status or "").strip().upper()
    if repair_authorizes_materialize(
        terminal_status=token,
        repaired_this_batch=repaired_this_batch,
        bug_batch_clear=bug_batch_clear,
    ):
        return None

    if is_repair_orchestration_blocking_terminal(token):
        return token, _orch_blocking_recovery_message(token)

    if token in {"PARTIAL_COMPLETE", "FAIL_CLOSED"}:
        return token, (
            f"Repair orch terminal STATUS: {token}; "
            "stopping before commit materialize or re-hunt."
        )

    if repaired_this_batch is False:
        message = "Orch completed with nothing repaired this batch; stopping loop."
        if token:
            message = f"{message} STATUS: {token}."
        return "NO_REPAIR_PROGRESS", message

    if token:
        return token, (
            f"Repair orch terminal STATUS: {token} does not authorize materialize; "
            "stopping before commit materialize or re-hunt."
        )
    return REPAIR_BLOCKED, (
        "Repair hop completed without materialize authorization; "
        "stopping before commit materialize or re-hunt."
    )


def repair_activity_allows_commit_materialize(repair: dict[str, Any]) -> bool:
    """True iff Stroy PBH gate would schedule ``execute_commit_materialize``."""
    if repair.get("uncertain"):
        return False
    if not repair.get("completed"):
        return False
    stop = classify_completed_repair(
        terminal_status=repair.get("terminal_status"),
        repaired_this_batch=repair.get("repaired_this_batch"),
        bug_batch_clear=repair.get("bug_batch_clear"),
    )
    return stop is None


def repair_activity_terminal_outcome(repair: dict[str, Any]) -> tuple[str, str] | None:
    """Return workflow terminal (status, message) or None if loop would continue."""
    if repair.get("uncertain"):
        return (
            "REPAIR_COMPLETION_UNCERTAIN",
            "Repair invocation ended ambiguously after possible side effects.",
        )
    if not repair.get("completed"):
        return (
            "REPAIR_FAILED",
            "Repair did not reach terminal status.",
        )
    stop = classify_completed_repair(
        terminal_status=repair.get("terminal_status"),
        repaired_this_batch=repair.get("repaired_this_batch"),
        bug_batch_clear=repair.get("bug_batch_clear"),
    )
    return stop
