"""Structured result models for Cursor agent invocations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ReviewRunResult:
    completed: bool
    clean: bool | None
    findings: list[dict[str, str]]
    actionable_findings: list[dict[str, str]]
    repairable_findings: list[dict[str, str]]
    excluded_findings: list[dict[str, str]]
    fingerprint: str | None
    raw_output: str
    transient_error: bool = False
    git_preflight_ok: bool = False
    candidate_sha: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed": self.completed,
            "clean": self.clean,
            "findings": self.findings,
            "actionable_findings": self.actionable_findings,
            "repairable_findings": self.repairable_findings,
            "excluded_findings": self.excluded_findings,
            "fingerprint": self.fingerprint,
            "raw_output": self.raw_output,
            "transient_error": self.transient_error,
            "git_preflight_ok": self.git_preflight_ok,
            "candidate_sha": self.candidate_sha,
        }


@dataclass
class RepairRunResult:
    completed: bool
    bug_batch_clear: bool | None
    unresolved: list[str]
    running_worker: str | None
    raw_output: str
    uncertain: bool = False
    repaired_this_batch: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "completed": self.completed,
            "bug_batch_clear": self.bug_batch_clear,
            "unresolved": self.unresolved,
            "running_worker": self.running_worker,
            "raw_output": self.raw_output,
            "uncertain": self.uncertain,
            "repaired_this_batch": self.repaired_this_batch,
        }


@dataclass
class LoopOutcome:
    status: str
    repair_rounds: int
    review_runs: int
    message: str = ""
    last_review: ReviewRunResult | None = None
    last_repair: RepairRunResult | None = None
    session_id: str | None = None
    review_history: list[ReviewRunResult] = field(default_factory=list)
    repair_history: list[RepairRunResult] = field(default_factory=list)
    last_materialize: dict[str, Any] | None = None
    candidate_sha: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "repair_rounds": self.repair_rounds,
            "review_runs": self.review_runs,
            "message": self.message,
            "session_id": self.session_id,
            "last_review": self.last_review.to_dict() if self.last_review else None,
            "last_repair": self.last_repair.to_dict() if self.last_repair else None,
            "last_materialize": self.last_materialize,
            "candidate_sha": self.candidate_sha,
        }
