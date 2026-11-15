"""Pydantic-модель ``test_triage_result`` для Test Triage Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agents.verify.static_triage.schemas import StaticTriageEvidenceRef


class TestTriageResult(BaseModel):
    """Классификация pytest finding: logic vs broken test (без fix)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    agent: Literal["test_triage"] = "test_triage"
    finding_id: str
    classification: Literal["LOGIC", "TEST", "UNCLEAR"]
    evidence_refs: StaticTriageEvidenceRef
    rationale: str = Field(min_length=1)
    change_slice_status: Literal["IN_DIFF", "NOT_IN_DIFF", "NO_GIT", "NO_PATH"]
    change_slice: str | None = None
