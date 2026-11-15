"""JSON-контракт resume-агента ``implementation_arbiter`` (directive-only)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ArbiterTargetStage = Literal[
    "APPLY_DESIGN",
    "APPLY_SCAFFOLD",
    "APPLY_RED",
    "APPLY_IMPLEMENT",
    "APPLY_GREEN",
]


class ArbiterDecision(BaseModel):
    """Направление после разбора блокера: целевой hop и текст для его prompt."""

    model_config = ConfigDict(extra="forbid")

    target_stage: ArbiterTargetStage | None = None
    target_slice_id: str | None = None
    directive: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    close_without_target: bool = False

    @model_validator(mode="after")
    def _target_required_unless_close(self) -> ArbiterDecision:
        if self.close_without_target:
            return self
        if not self.target_stage:
            msg = "target_stage is required when close_without_target is false"
            raise ValueError(msg)
        return self
