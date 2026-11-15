"""Общий контракт эскалации implementation hop → ``implementation_arbiter``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SplitApplyHopStatus = Literal[
    "APPLY_COMPLETE",
    "APPLY_PARTIAL",
    "BLOCKED",
    "NO_CHANGE",
    "ESCALATE_ARBITER",
]

DesignReviewHopStatus = Literal[
    "APPLY_DESIGN_REVIEW_PASS",
    "REPAIRS_REQUIRED",
    "ESCALATE_ARBITER",
]


class ArbiterRequest(BaseModel):
    """Запрос implement-агента на разбор арбитром (без прямых правок design/кода)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    blocker_code: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    suggested_target_stage: str | None = None


def validate_split_apply_arbiter_fields(
    status: str,
    arbiter_request: ArbiterRequest | None,
) -> ArbiterRequest | None:
    """Проверить связку ``status`` / ``arbiter_request`` для split-apply hop."""
    if status == "ESCALATE_ARBITER":
        if arbiter_request is None:
            msg = "arbiter_request is required when status=ESCALATE_ARBITER"
            raise ValueError(msg)
        return arbiter_request
    if arbiter_request is not None:
        msg = "arbiter_request is forbidden unless status=ESCALATE_ARBITER"
        raise ValueError(msg)
    return None
