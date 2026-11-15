"""Typed ответ SDK для узла scenario-coverage plan."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScenarioCoveragePlanAnswer(BaseModel):
    """Статус mutating hop планировщика сценариев."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["PLAN_READY", "PLAN_PARTIAL", "BLOCKED"]
    block_reason: str | None = None
    scenario_count: int = Field(default=0, ge=0)
