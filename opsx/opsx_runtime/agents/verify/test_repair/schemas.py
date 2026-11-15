"""Pydantic-модель результата Test Repair Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TestRepairResult(BaseModel):
    """Исправление теста для одного pytest finding (не bug workflow)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    agent: Literal["test_repair"] = "test_repair"
    finding_id: str
    input_fingerprint: str | None = None
    status: Literal["FIXED", "FAILED", "BLOCKED", "CANNOT_COMPLETE"]
    summary: str = Field(min_length=1)
    files_changed: list[str] = Field(default_factory=list)
    notes: str | None = None
