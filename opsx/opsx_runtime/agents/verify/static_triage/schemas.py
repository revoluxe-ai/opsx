"""Pydantic-модель ``static_triage_result`` для Static Triage Agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StaticTriageEvidenceRef(BaseModel):
    """Ссылки на место ошибки и наличие hunk в change slice."""

    model_config = ConfigDict(extra="forbid")

    path: str | None = None
    line: int | None = None
    change_hunk_present: bool = False


class StaticTriageResult(BaseModel):
    """Классификация static finding: logic vs typing (без fix и executor)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    agent: Literal["static_triage"] = "static_triage"
    finding_id: str
    classification: Literal["LOGIC", "TYPING", "UNCLEAR"]
    evidence_refs: StaticTriageEvidenceRef
    rationale: str = Field(min_length=1)
    change_slice_status: Literal["IN_DIFF", "NOT_IN_DIFF", "NO_GIT", "NO_PATH"]
    change_slice: str | None = None
