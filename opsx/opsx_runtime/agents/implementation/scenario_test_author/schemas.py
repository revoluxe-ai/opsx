"""Typed ответ SDK для authoring canonical scenario tests."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ScenarioTestAuthorAnswer(BaseModel):
    """Статус hop авторинга сценарных тестов."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["AUTHOR_COMPLETE", "AUTHOR_PARTIAL", "BLOCKED"]
    block_reason: str | None = None
    authored_count: int = Field(default=0, ge=0)
