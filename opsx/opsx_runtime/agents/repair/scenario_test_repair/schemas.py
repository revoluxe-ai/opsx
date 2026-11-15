"""Strict result contract for a scenario-test-only repair hop."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScenarioTestRepairAnswer(BaseModel):
    """Model's repair *candidate*, never independent verification evidence."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["CANDIDATE_PENDING_VERIFY", "PARTIAL", "BLOCKED", "NO_CHANGE"]
    summary: str = Field(min_length=1)
    addressed_gap_ids: list[str] = Field(default_factory=list)
    remaining_gap_ids: list[str] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    checks_reported: list[str] = Field(
        default_factory=list,
        description="Model-reported checks only; not a machine-verified test receipt.",
    )
    unverified_tests: list[str] = Field(default_factory=list)
    recovery_contract: str = Field(min_length=1)

    @model_validator(mode="after")
    def consistent(self) -> "ScenarioTestRepairAnswer":
        if len(set(self.addressed_gap_ids)) != len(self.addressed_gap_ids):
            raise ValueError("duplicate addressed_gap_ids")
        if len(set(self.remaining_gap_ids)) != len(self.remaining_gap_ids):
            raise ValueError("duplicate remaining_gap_ids")
        if set(self.addressed_gap_ids) & set(self.remaining_gap_ids):
            raise ValueError("addressed/remaining gaps overlap")
        if len(set(self.files_changed)) != len(self.files_changed):
            raise ValueError("duplicate files_changed")
        if self.status == "CANDIDATE_PENDING_VERIFY":
            if not self.files_changed or not self.addressed_gap_ids or self.remaining_gap_ids:
                raise ValueError("CANDIDATE requires changed tests, addressed gaps, no remaining")
        if self.status == "PARTIAL" and not self.remaining_gap_ids:
            raise ValueError("PARTIAL requires unresolved gaps")
        if self.status == "NO_CHANGE" and self.files_changed:
            raise ValueError("NO_CHANGE cannot report changes")
        if self.status == "BLOCKED" and self.addressed_gap_ids and not self.files_changed:
            raise ValueError("BLOCKED cannot claim repairs without changed tests")
        return self
