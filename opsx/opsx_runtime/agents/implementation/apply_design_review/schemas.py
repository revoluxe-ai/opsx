"""JSON-контракт runtime-агента apply-design-review hop (split-apply pipeline)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.implementation.arbiter_contract import ArbiterRequest


class ApplyDesignReviewAnswer(BaseModel):
    """Итог одного apply-design-review hop. Следующую стадию выбирает граф по ``status``, не агент."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "APPLY_DESIGN_REVIEW_PASS",
        "REPAIRS_REQUIRED",
        "ESCALATE_ARBITER",
    ]
    review_summary: str = Field(min_length=1)
    findings_summary: str = Field(min_length=1)
    files_changed: list[str] = Field(default_factory=list)
    blocker_codes: list[str] = Field(default_factory=list)
    arbiter_request: ArbiterRequest | None = None

    @model_validator(mode="after")
    def _arbiter_fields(self) -> ApplyDesignReviewAnswer:
        if self.status == "ESCALATE_ARBITER":
            if self.arbiter_request is None:
                msg = "arbiter_request is required when status=ESCALATE_ARBITER"
                raise ValueError(msg)
        elif self.arbiter_request is not None:
            msg = "arbiter_request is forbidden unless status=ESCALATE_ARBITER"
            raise ValueError(msg)
        return self
