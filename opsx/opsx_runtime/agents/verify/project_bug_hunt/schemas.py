"""Схемы JSON-ответа ProjectBugHuntAgent для verify LangGraph."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProductionClassification = Literal[
    "confirmed production bug",
    "reachable but low-risk",
    "unclear reachability",
    "dead legacy",
    "tests-only",
    "unreachable",
    "adjacent",
    "false positive",
]

HuntStatus = Literal[
    "HUNT_COMPLETE",
    "BLOCKED_NEEDS_INPUT",
    "BLOCKED_PREFLIGHT",
    "EVIDENCE_GAP",
]


class ProjectBugHuntFinding(BaseModel):
    """Один finding project-bug-hunt с классификацией и LOC_LINK."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    loc_link: str = Field(min_length=1, description="repo-relative path:line")
    production_classification: ProductionClassification
    literal_claim: str = Field(min_length=1)
    runtime_path: str = Field(default="", description="краткое описание reachability")
    separate_adjudication_required: bool = True


class ProjectBugHuntAnswer(BaseModel):
    """Структурированный результат READ_ONLY project bug hunt."""

    model_config = ConfigDict(extra="forbid")

    status: HuntStatus
    mode: Literal["READ_ONLY"] = "READ_ONLY"
    findings: list[ProjectBugHuntFinding] = Field(default_factory=list)
    block_reason: str | None = None
    confirmed_production_bug_count: int = 0

    @model_validator(mode="after")
    def check_consistency(self) -> "ProjectBugHuntAnswer":
        confirmed = sum(
            1
            for row in self.findings
            if row.production_classification == "confirmed production bug"
        )
        if self.confirmed_production_bug_count != confirmed:
            raise ValueError(
                "confirmed_production_bug_count must match findings classification"
            )
        if self.status == "HUNT_COMPLETE":
            if self.block_reason:
                raise ValueError("HUNT_COMPLETE must not include block_reason")
        elif not self.block_reason:
            raise ValueError(f"{self.status} requires block_reason")
        return self
