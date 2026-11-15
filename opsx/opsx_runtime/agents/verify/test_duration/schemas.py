"""Схема агента длительности: зависание кода, зависание теста или персональный таймаут."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.verify.static_triage.schemas import StaticTriageEvidenceRef


class TestDurationResult(BaseModel):
    """Классификация одного timeout/stall finding. Правки кода агент не делает."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    agent: Literal["test_duration"] = "test_duration"
    finding_id: str
    classification: Literal["HANG_PRODUCT", "HANG_TEST", "PERSONAL_TIMEOUT", "UNCLEAR"]
    evidence_refs: StaticTriageEvidenceRef
    rationale: str = Field(min_length=1)
