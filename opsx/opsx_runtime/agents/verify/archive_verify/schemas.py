"""Контракт JSON-ответа агента ``/opsx-verify mode=archive`` (PHASE_ARCHIVE)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VerifyArchiveAnswer(BaseModel):
    """Структурированный archive-вердикт: готовность change к ``/opsx-archive``."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["GO", "NO_GO"]
    completeness_summary: str = Field(min_length=1)
    correctness_summary: str = Field(min_length=1)
    coherence_summary: str = Field(min_length=1)
    product_acceptance_status: Literal[
        "PASS",
        "FAIL",
        "BLOCKED",
        "NOT_APPLICABLE",
        "NOT_VERIFIED",
    ] = "NOT_VERIFIED"
    gap_details: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_command: str = Field(
        default="",
        description="Не для маршрутизации графа; оставьте пустой строкой.",
    )
    local_hop_copy_block: str = Field(
        min_length=1,
        description="Owner-local блок: workspace, change, mode=archive, marker, gaps.",
    )
