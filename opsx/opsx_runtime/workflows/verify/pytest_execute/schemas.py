"""DTO workflow ``pytest_execute`` — единственная публичная точка прогона pytest на scope."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from workflows.verify.gate_repair.schemas import GateCheckResult

ScopeKind = Literal["MODULES", "FULL_SUITE", "COHORT"]


class PytestExecuteRequest(BaseModel):
    """Запрос на collect-loop + optional harness repair + pytest run."""

    model_config = ConfigDict(extra="forbid")

    scope_kind: ScopeKind = "MODULES"
    module_paths: list[str] = Field(default_factory=list)
    max_harness_attempts: int = Field(default=3, ge=0, le=10)
    collect_only: bool = False
    timeout_seconds: float | None = None


class CollectProof(BaseModel):
    """Доказательство collect-only для closure harness findings."""

    model_config = ConfigDict(extra="forbid")

    scope_key: str
    collect_ok: bool
    collect_exit_code: int
    collect_tail: str = ""
    attempts_used: int = 0


class PytestExecuteResult(BaseModel):
    """Итог ``run_pytest_execute``."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["PASS", "FAIL", "PAUSE"]
    scope_key: str
    collect_proof: CollectProof
    gate_check: GateCheckResult | None = None
    harness_attempts: int = 0
    files_changed: list[str] = Field(default_factory=list)
    detail: str = ""
    run_log: str = ""
    owner_attention: str | None = None
