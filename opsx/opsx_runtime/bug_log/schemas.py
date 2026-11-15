"""Схемы кандидата, наблюдения и событий журнала багов."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChangeScope = Literal["CHANGE", "STANDALONE", "META"]
EventKind = Literal[
    "BUG_REGISTERED",
    "SIGHTING",
    "ALIAS_ADDED",
    "LINKED",
    "CLASSIFIED",
    "HOP_RESULT",
    "STATUS_CHANGED",
    "EPISODE_OPENED",
    "EPISODE_CLOSED",
    "EVIDENCE_ATTACHED",
    "ANOMALY",
]


class JournalScope(BaseModel):
    """Контекст прогона, заданный один раз при открытии журнала."""

    model_config = ConfigDict(extra="forbid")

    repo_root: str
    change_id: str | None = None
    change_scope: ChangeScope = "STANDALONE"
    run_id: str
    stream_log: str | None = None
    mission_id: str | None = None


class Provenance(BaseModel):
    """Кто записал событие."""

    model_config = ConfigDict(extra="forbid")

    agent_role: str | None = None
    hop: str | None = None
    execution_id: str | None = None
    attempt: int | None = None
    model_id: str | None = None
    lane: str = "unknown"


class BugCandidate(BaseModel):
    """Запрос на выдачу или поиск bug_id."""

    model_config = ConfigDict(extra="forbid")

    lane: str
    observation: str = Field(min_length=1)
    location: str = ""
    tool: str | None = None
    local_id: str | None = None
    alias_scheme: str | None = None
    severity: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class BugObservation(BaseModel):
    """Факт о уже известном баге (hop, статус, evidence)."""

    model_config = ConfigDict(extra="forbid")

    bug_id: str
    kind: EventKind = "HOP_RESULT"
    summary: str
    status: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance = Field(default_factory=Provenance)
    related_bug_id: str | None = None
    relation: str | None = None


class BugSummary(BaseModel):
    """Снимок бага для read-only контекста модели."""

    model_config = ConfigDict(extra="forbid")

    bug_id: str
    status: str
    first_seen_change: str | None = None
    recurrence_count: int = 0
    fingerprint: str


class JournalHealth(BaseModel):
    """Счётчики записи для итога прогона."""

    model_config = ConfigDict(extra="forbid")

    events: int = 0
    spooled: int = 0
    dropped: int = 0
