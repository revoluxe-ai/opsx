"""Pydantic-модель результата Typing Repair Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TypingRepairResult(BaseModel):
    """Исправление typing/static-analysis для одного finding (не bug workflow)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    agent: Literal["typing_repair"] = "typing_repair"
    finding_id: str
    status: Literal["FIXED", "FAILED", "BLOCKED"]
    summary: str = Field(min_length=1)
    files_changed: list[str] = Field(default_factory=list)
    notes: str | None = None
