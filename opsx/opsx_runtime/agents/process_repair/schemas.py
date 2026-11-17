"""Typed payloads супервизорного fix/commit."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProcessQualifierVerdict(BaseModel):
    """Вердикт qualifier-hop супервизора (без правок файлов)."""

    model_config = ConfigDict(extra="forbid")

    owner: str
    code: str
    stage: str
    fingerprint: str
    root_cause: str
    message: str
    fix_hop_allowed: bool


class ProcessFixReport(BaseModel):
    """Отчёт ProcessFixAgent для журнала супервизора."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["FIXED", "NOT_META", "CANNOT_FIX", "FIX_INTERRUPTED"]
    root_cause: str = ""
    owner_surface: str = ""
    files_changed: list[str] = Field(default_factory=list)
    checks_run: list[dict[str, str]] = Field(default_factory=list)
    restart_required: bool = True
    error: str | None = None
    solver_action: Literal["REPAIR_AGENT"] | None = None
    repair_agent: str = ""


class CommitJournalResult(BaseModel):
    """Результат детерминированного commit_journal_entry."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["COMMITTED", "BLOCKED", "FAILED"]
    commit_sha: str | None = None
    error: str | None = None