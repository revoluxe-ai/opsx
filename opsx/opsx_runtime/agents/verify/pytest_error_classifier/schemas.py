"""Схемы pytest_error_classifier (MVP-1 enrichment)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PytestErrorLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    axis: Literal["BUILD", "TEST", "UNCLEAR"]
    confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    rationale: str = ""


class PytestErrorClassifierResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["CLASSIFIED", "PAUSE", "PARTIAL"] = "CLASSIFIED"
    labels: list[PytestErrorLabel] = Field(default_factory=list)
