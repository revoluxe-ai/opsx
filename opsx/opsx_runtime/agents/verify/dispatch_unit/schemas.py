"""Схемы группового triage и статусов проверки findings Repair Unit."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CausalRepairGroup(BaseModel):
    """Одна причинная группа findings после группового triage."""

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1)
    finding_ids: list[str] = Field(min_length=1)
    classification: Literal["LOGIC", "TYPING", "TEST", "UNCLEAR"]
    repair_route: Literal["static", "test", "test_harness", "personal_timeout"]
    rationale: str = Field(min_length=1)


class RepairUnitTriageResult(BaseModel):
    """Ответ группового triage (Static/Test Triage в режиме repair unit)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.1", "1.2"] = "1.2"
    agent: Literal["static_triage", "test_triage"] = "static_triage"
    repair_unit_key: str
    anchor_finding_id: str
    status: Literal["GROUPED", "PAUSE"]
    causal_groups: list[CausalRepairGroup] = Field(default_factory=list)
    pause_reason: str | None = None


FindingVerifyStatus = Literal[
    "RESOLVED",
    "UNRESOLVED",
    "STALE",
    "UNKNOWN",
    "ALREADY_RESOLVED",
]


class FindingMemberStatus(BaseModel):
    """Детерминированный статус одного finding после focused verify."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    status: FindingVerifyStatus
    detail: str = ""
