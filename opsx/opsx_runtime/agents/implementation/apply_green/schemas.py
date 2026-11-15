"""JSON-контракт runtime-агента apply-green hop (команда ``/opsx-apply-green``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.implementation.arbiter_contract import (
    ArbiterRequest,
    validate_split_apply_arbiter_fields,
)


class ApplyGreenAnswer(BaseModel):
    """Итог одного apply-green hop. Следующую стадию выбирает граф по ``status``, не агент."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "APPLY_COMPLETE",
        "APPLY_PARTIAL",
        "BLOCKED",
        "NO_CHANGE",
        "ESCALATE_ARBITER",
    ]
    tasks_summary: str = Field(min_length=1)
    gap_closure_summary: str = Field(min_length=1)
    files_changed: list[str] = Field(default_factory=list)
    remaining_gaps: list[str] = Field(default_factory=list)
    arbiter_request: ArbiterRequest | None = None

    @model_validator(mode="after")
    def _arbiter_fields(self) -> ApplyGreenAnswer:
        validate_split_apply_arbiter_fields(self.status, self.arbiter_request)
        return self
