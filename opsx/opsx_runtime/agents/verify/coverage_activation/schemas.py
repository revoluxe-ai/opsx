"""Контракт ответа CoverageActivationAgent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CoverageActivationVerdict = Literal[
    "COVERAGE_ACTIVATION_PASS",
    "COVERAGE_ACTIVATION_BLOCKED",
    "COVERAGE_ACTIVATION_NEEDS_OWNER",
]


class CoverageActivationAnswer(BaseModel):
    """Structured verdict после inventory + activation hop."""

    model_config = ConfigDict(extra="forbid")

    verdict: CoverageActivationVerdict
    summary: str = Field(min_length=1)
    approve_test_reactivation: list[str] = Field(default_factory=list)
    approve_static_paths: list[str] = Field(default_factory=list)
    block_reason: str = Field(default="")
    test_coverage_gaps: list[str] = Field(default_factory=list)
    local_hop_copy_block: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_consistency(self) -> "CoverageActivationAnswer":
        """``block_reason`` информационный (inventory, exclude); не останавливает verify."""
        if self.verdict in (
            "COVERAGE_ACTIVATION_BLOCKED",
            "COVERAGE_ACTIVATION_NEEDS_OWNER",
        ) and not self.block_reason.strip():
            raise ValueError("BLOCKED/NEEDS_OWNER requires block_reason")
        return self
