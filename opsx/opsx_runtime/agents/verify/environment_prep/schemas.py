"""Pydantic-модели environment_prep (verify first-pass harness + env)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EnvironmentPrepKind = Literal[
    "hermetic_boundary",
    "shared_fixtures",
    "bootstrap_keys",
    "pg_env",
    "syntax_collection",
    "generic_collection",
]

EnvironmentPrepStatus = Literal[
    "FIXED",
    "PARTIAL",
    "FAILED",
    "BLOCKED",
    "NO_ACTION",
]


class EnvironmentPrepCohort(BaseModel):
    """Одна когорта системных ошибок gates."""

    model_config = ConfigDict(extra="forbid")

    cohort_id: str
    kind: EnvironmentPrepKind
    priority: int
    finding_ids: list[str] = Field(default_factory=list)
    sample_observations: list[str] = Field(default_factory=list)


class EnvironmentPrepCohorts(BaseModel):
    """Детерминированный список когорт для LLM-hop."""

    model_config = ConfigDict(extra="forbid")

    cohorts: list[EnvironmentPrepCohort] = Field(default_factory=list)
    collection_finding_count: int = 0


class EnvironmentPrepCollectProof(BaseModel):
    """Collect-only доказательство после hop."""

    model_config = ConfigDict(extra="forbid")

    modules: list[str] = Field(default_factory=list)
    exit_code: int = 0
    collect_ok: bool = False


class EnvironmentPrepResult(BaseModel):
    """Результат VerifyEnvironmentPrepAgent."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    agent: Literal["verify_environment_prep"] = "verify_environment_prep"
    status: EnvironmentPrepStatus
    summary: str = Field(min_length=1)
    files_changed: list[str] = Field(default_factory=list)
    collect_proof: EnvironmentPrepCollectProof | None = None
    cohorts_addressed: list[str] = Field(default_factory=list)
    environment_report: str | None = None
    gates_rerun_recommended: bool = False
    notes: str | None = None
