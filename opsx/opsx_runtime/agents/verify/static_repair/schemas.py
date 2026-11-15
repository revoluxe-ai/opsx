"""Deterministic static-repair hop после quality gates (очередь issues, без LLM)."""

from __future__ import annotations

from typing import Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class StaticRepairQueueItem(BaseModel):
    """Один item для downstream fix/static-repair workflow."""

    model_config = ConfigDict(extra="forbid")

    tool: str = Field(min_length=1)
    issue: str = Field(min_length=1)


class StaticRepairHandoff(TypedDict, total=False):
    """Patch в ``GraphState`` после ``StaticRepairAgent``."""

    static_repair_status: str
    static_repair_marker: str
    static_repair_report_text: str
    static_repair_queue: list[dict[str, str]]
    static_repair_issue_count: int
