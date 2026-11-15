"""Маппинг OPSX terminal status → видимый исход Temporal для review/repair loop.

Только семантика миссии: успех — clean_status lane (например HUNT_CLEAN).
Engineering failures не должны отображаться как COMPLETED в Temporal UI.

Stroy port-branch status tokens (donor ``models.py`` at Game e07907b does not define these).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Port-branch review terminal tokens (string contract; not part of donor models API).
REVIEW_FAILED = "REVIEW_FAILED"
REVIEW_UNPARSEABLE = "REVIEW_UNPARSEABLE"
REVIEW_CONTRACT_INVALID = "REVIEW_CONTRACT_INVALID"
REVIEW_BLOCKED = "REVIEW_BLOCKED"

TEMPORAL_MISSION_COMPLETE = "REVIEW_REPAIR_MISSION_COMPLETE"
TEMPORAL_MISSION_FAILED = "REVIEW_REPAIR_MISSION_FAILED"
TEMPORAL_MISSION_PAUSED = "REVIEW_REPAIR_MISSION_PAUSED"

_FAILURE_APPLICATION_TYPE = "ProjectBugHuntTerminalFailure"


@dataclass(frozen=True)
class ReviewRepairTerminalMapping:
    """Решение по одному OPSX status."""

    opsx_status: str
    temporal_status: str
    temporal_execution_failed: bool
    non_retryable: bool
    bucket: str  # PASS | FAIL | PAUSE | STOP


def map_project_bug_hunt_terminal(
    opsx_status: str | None,
    *,
    clean_status: str = "HUNT_CLEAN",
) -> ReviewRepairTerminalMapping:
    """Детерминированный маппинг для Project Bug Hunt."""
    token = (opsx_status or "").strip()
    if token == clean_status:
        return ReviewRepairTerminalMapping(
            opsx_status=token,
            temporal_status=TEMPORAL_MISSION_COMPLETE,
            temporal_execution_failed=False,
            non_retryable=True,
            bucket="PASS",
        )
    if token in {
        REVIEW_CONTRACT_INVALID,
        REVIEW_UNPARSEABLE,
        REVIEW_FAILED,
        "REPAIR_FAILED",
        "STALLED",
        "NO_REPAIR_PROGRESS",
        "MAX_ROUNDS_REACHED",
        "FAIL_CLOSED_RECURRING_CAUSE",
    }:
        return ReviewRepairTerminalMapping(
            opsx_status=token,
            temporal_status=TEMPORAL_MISSION_FAILED,
            temporal_execution_failed=True,
            non_retryable=True,
            bucket="FAIL",
        )
    if (
        token == REVIEW_BLOCKED
        or token.startswith("BLOCKED_")
        or token.startswith("PAUSE")
        or token in {"STALE_WORKER", "WORKER_CODE_IDENTITY_UNVERIFIED"}
    ):
        return ReviewRepairTerminalMapping(
            opsx_status=token,
            temporal_status=TEMPORAL_MISSION_PAUSED,
            temporal_execution_failed=True,
            non_retryable=True,
            bucket="PAUSE",
        )
    # Unknown / legacy repair terminals — fail closed for hunt mission visibility.
    return ReviewRepairTerminalMapping(
        opsx_status=token or "UNKNOWN",
        temporal_status=TEMPORAL_MISSION_FAILED,
        temporal_execution_failed=True,
        non_retryable=True,
        bucket="FAIL",
    )


def enrich_terminal_payload(
    payload: dict[str, Any],
    mapping: ReviewRepairTerminalMapping,
    *,
    worker_code_identity: str | None = None,
    recovery_instructions: str | None = None,
) -> dict[str, Any]:
    """Добавить temporal/evidence поля без потери исходного OPSX status."""
    enriched = dict(payload)
    enriched["terminal_status"] = mapping.opsx_status
    enriched["temporal_status"] = mapping.temporal_status
    enriched["terminal_bucket"] = mapping.bucket
    if worker_code_identity:
        enriched["worker_code_identity"] = worker_code_identity
    if recovery_instructions:
        enriched["recovery_instructions"] = recovery_instructions
    return enriched


def finalize_project_bug_hunt_workflow_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Завершить workflow: обогатить payload и поднять non-retryable failure при FAIL."""
    from temporalio.exceptions import ApplicationError

    mapping = map_project_bug_hunt_terminal(str(payload.get("status") or ""))
    enriched = enrich_terminal_payload(payload, mapping)
    if mapping.temporal_execution_failed:
        raise ApplicationError(
            json.dumps(enriched, ensure_ascii=False),
            type=_FAILURE_APPLICATION_TYPE,
            non_retryable=mapping.non_retryable,
        )
    return enriched


def terminal_payload_from_application_error(exc: BaseException) -> dict[str, Any] | None:
    """Извлечь terminal evidence из ApplicationError workflow (operator / start.py)."""
    from temporalio.exceptions import ApplicationError, FailureError

    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ApplicationError) and current.type == _FAILURE_APPLICATION_TYPE:
            try:
                data = json.loads(str(current.message))
            except json.JSONDecodeError:
                return {"status": "UNKNOWN", "message": str(current.message)}
            return data if isinstance(data, dict) else None
        if isinstance(current, FailureError) and current.cause is not None:
            current = current.cause
            continue
        break
    return None
