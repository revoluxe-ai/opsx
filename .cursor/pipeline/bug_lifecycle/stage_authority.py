"""Stage preconditions and SSCA for auto_persist (no arbitrary stage jumps)."""

from __future__ import annotations

from enum import Enum
from typing import Any


class Stage(str, Enum):
    S0 = "S0_INTAKE"
    S1B = "S1b_FAMILY_PLAN"
    S2B = "S2b_DISPOSITION"
    S3 = "S3_ROUTE_DISPOSITION"
    S3_5 = "S3_5_WORKTREE_OWNERSHIP"
    S4 = "S4_BUG_TEAM"
    S5A = "S5a_CLOSURE_EVAL"
    S5B = "S5b_MATERIALIZE_CLOSE"
    S6 = "S6_NON_REPAIR_DISPOSITION"
    S7 = "S7_EVOLUTION_QUEUE"
    S8 = "S8_EVOLUTION_HANDOFF"
    S9 = "S9_DEBT_BOARD"
    S10 = "S10_BATCH_CLEAR"
    S11 = "S11_RETURN_TO_CALLER"
    DONE = "BUG_BATCH_CLEAR"


ORDER: list[Stage] = [
    Stage.S0,
    Stage.S1B,
    Stage.S2B,
    Stage.S3,
    Stage.S3_5,
    Stage.S4,
    Stage.S5A,
    Stage.S5B,
    Stage.S6,
    Stage.S7,
    Stage.S8,
    Stage.S9,
    Stage.S10,
    Stage.S11,
    Stage.DONE,
]


def next_stage(stage: Stage) -> Stage | None:
    try:
        idx = ORDER.index(stage)
    except ValueError:
        return None
    if idx + 1 >= len(ORDER):
        return None
    return ORDER[idx + 1]


def auto_persist_stage_for_command(command: str) -> Stage | None:
    lowered = command.lower()
    if "family-plan" in lowered:
        return Stage.S1B
    if "disposition" in lowered:
        return Stage.S2B
    if "materialize-bug-queue" in lowered:
        return Stage.S5B
    return None


def record_stage_complete(state: dict[str, Any], completed: Stage) -> None:
    done = state.setdefault("completed_stages", [])
    if not isinstance(done, list):
        done = []
        state["completed_stages"] = done
    token = completed.value
    if token not in done:
        done.append(token)


def ssca_allows_advance(state: dict[str, Any], stage: Stage) -> tuple[bool, str]:
    current = str(state.get("stage") or Stage.S0.value)
    if current != stage.value:
        return False, "stage_mismatch"
    done = state.get("completed_stages") or []
    if not isinstance(done, list):
        return False, "invalid_completed_stages"
    try:
        idx = ORDER.index(stage)
    except ValueError:
        return False, "unknown_stage"
    if idx == 0:
        return True, "ok"
    prior = ORDER[idx - 1].value
    if prior not in done:
        return False, "ssca_prior_incomplete"
    return True, "ok"
