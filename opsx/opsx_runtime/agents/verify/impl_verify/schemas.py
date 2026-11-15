"""Контракт JSON-ответа агента ``/opsx-verify mode=impl`` (PHASE_IMPL)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VerifyImplAnswer(BaseModel):
    """Структурированный вердикт: совпадает ли реализация change с его планом (без test coverage)."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["IMPL_OK", "IMPL_GAPS"]
    behavior_summary: str = Field(min_length=1)
    architecture_placement: str = Field(min_length=1)
    tasks_consistency: str = Field(min_length=1)
    gap_details: list[str] = Field(default_factory=list)
    next_command: str = Field(
        default="",
        description="Не для маршрутизации графа; оставьте пустой строкой.",
    )
    local_hop_copy_block: str = Field(
        min_length=1,
        description="Owner-local блок: workspace, change, marker, gaps (без выдуманных slash-команд).",
    )
