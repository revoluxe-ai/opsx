"""Pydantic-модель результата Test Harness Repair Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HarnessCollectProof(BaseModel):
    """Collect-only доказательство после hop."""

    model_config = ConfigDict(extra="forbid")

    modules: list[str] = Field(default_factory=list)
    exit_code: int = 0


class TestHarnessRepairResult(BaseModel):
    """Восстановление collection/syntax в ``tests/**``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    agent: Literal["test_harness_repair"] = "test_harness_repair"
    status: Literal["FIXED", "FAILED", "BLOCKED"]
    summary: str = Field(min_length=1)
    files_changed: list[str] = Field(default_factory=list)
    collect_proof: HarnessCollectProof | None = None
    notes: str | None = None
