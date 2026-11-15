"""Схемы JSON-ответа SecurityReviewAgent для verify LangGraph."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SecuritySeverity = Literal["critical", "high", "medium", "low", "informational"]

SecurityClassification = Literal[
    "confirmed security issue",
    "informational",
    "process_lane",
    "false positive",
]

SecurityReviewStatus = Literal[
    "SECURITY_COMPLETE",
    "BLOCKED_NEEDS_INPUT",
    "BLOCKED_PREFLIGHT",
    "EVIDENCE_GAP",
]


class SecurityReviewFinding(BaseModel):
    """Один security finding с severity и классификацией для repair mapper."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(min_length=1)
    severity: SecuritySeverity
    location: str = Field(min_length=1, description="repo-relative path:line")
    finding: str = Field(min_length=1)
    security_classification: SecurityClassification


class SecurityReviewAnswer(BaseModel):
    """Структурированный результат READ_ONLY security review (/review-security)."""

    model_config = ConfigDict(extra="forbid")

    status: SecurityReviewStatus
    mode: Literal["READ_ONLY"] = "READ_ONLY"
    review_scope: Literal["SRC_TESTS_ONLY"] = "SRC_TESTS_ONLY"
    base_branch: str = Field(default="stage", min_length=1)
    findings: list[SecurityReviewFinding] = Field(default_factory=list)
    block_reason: str | None = None
    confirmed_security_issue_count: int = 0

    @model_validator(mode="after")
    def check_consistency(self) -> "SecurityReviewAnswer":
        confirmed = sum(
            1
            for row in self.findings
            if row.security_classification == "confirmed security issue"
        )
        if self.confirmed_security_issue_count != confirmed:
            raise ValueError(
                "confirmed_security_issue_count must match findings classification"
            )
        if self.status == "SECURITY_COMPLETE":
            if self.block_reason:
                raise ValueError("SECURITY_COMPLETE must not include block_reason")
        elif not self.block_reason:
            raise ValueError(f"{self.status} requires block_reason")
        return self
