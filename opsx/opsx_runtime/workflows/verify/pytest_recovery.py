"""MVP-1: единая точка recovery при harness debt / cohort UNKNOWN."""

from __future__ import annotations

from typing import Literal

from agents.state import GraphState
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.pytest_run_health import is_harness_blocker

RecoveryAction = Literal["ENQUEUE_HARNESS", "PAUSE", "CONTINUE"]


def resolve_pytest_recovery_action(state: GraphState) -> RecoveryAction:
    """Решить, нужен ли harness recovery до terminal PAUSE."""
    attempts = int(state.get("verify_harness_recovery_attempts") or 0)
    if attempts >= 2:
        return "PAUSE"
    raw = state.get("analysis_result")
    if isinstance(raw, dict):
        analysis = GroupErrorResult.model_validate(raw)
        if any(is_harness_blocker(f.repair_workflow) for f in analysis.findings):
            return "ENQUEUE_HARNESS"
    return "CONTINUE"
