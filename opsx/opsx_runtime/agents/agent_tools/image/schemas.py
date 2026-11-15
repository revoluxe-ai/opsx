"""Pydantic-схемы image_tasks и результатов hop."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ImageGenerationMode(StrEnum):
    """Режим генерации из design task."""

    COMPOSE_ONLY = "compose_only"
    GENERATIVE_REQUIRED = "generative_required"
    AGENT_CHOICE = "agent_choice"


class ImageTaskAcceptance(BaseModel):
    """Технические и визуальные критерии приёмки одной задачи."""

    ihdr: list[int] = Field(min_length=2, max_length=2)
    registry_member: bool = False
    visual_rubric: str = Field(min_length=1)


class ImageDesignTask(BaseModel):
    """Один элемент ``image_tasks`` в slice APPLY-DESIGN."""

    task_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    output_path: str = Field(min_length=1)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    format: str = "png"
    copy_text: list[str] = Field(default_factory=list)
    style_refs: list[str] = Field(default_factory=list)
    product_dependencies: list[str] = Field(default_factory=list)
    acceptance: ImageTaskAcceptance
    generation_mode: ImageGenerationMode = ImageGenerationMode.AGENT_CHOICE
    slice_id: str | None = None
    repair_max_attempts: int | None = None
    repair_deadline_seconds: int | None = None


class VisualFinding(BaseModel):
    """Структурированное замечание visual review."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: str = "major"


class ImageAssetHopMode(StrEnum):
    CREATE = "create"
    REPAIR = "repair"


class ImageAssetHopInput(BaseModel):
    """Вход child hop ImageAssetAgent."""

    task: ImageDesignTask
    mode: ImageAssetHopMode = ImageAssetHopMode.CREATE
    visual_findings: list[VisualFinding] = Field(default_factory=list)
    prior_output_revision: str | None = None
    allowlisted_output: str


class VisualReviewVerdict(BaseModel):
    """Исход visual review sub-hop."""

    status: str
    findings: list[VisualFinding] = Field(default_factory=list)
    summary: str = ""


class TechnicalGateVerdict(BaseModel):
    """Детерминированная техническая проверка PNG."""

    ok: bool
    output_revision: str
    errors: list[str] = Field(default_factory=list)


class ImageAssetTaskResult(BaseModel):
    """Результат ``execute_image_task`` для parent hop."""

    status: str
    task_id: str
    technical: TechnicalGateVerdict | None = None
    visual: VisualReviewVerdict | None = None
    output_path: str | None = None
    receipt_path: str | None = None
    code: str | None = None
    message: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
