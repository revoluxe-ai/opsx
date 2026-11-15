"""Маппинг ProjectBugHuntAnswer → FindingReport для bug-team repair."""

from __future__ import annotations

from typing import Any, Mapping

from agents.verify.project_bug_hunt.schemas import ProjectBugHuntAnswer
from agents.state import BugBotAnswer
from workflows.repair.schemas import FindingReport, SourceFinding

CONFIRMED_PRODUCTION_BUG = "confirmed production bug"


def bugbot_review_to_finding_report(review: Mapping[str, Any]) -> FindingReport:
    """BugBot JSON review → ``FindingReport`` (все findings из FINDINGS)."""
    parsed = BugBotAnswer.model_validate(dict(review))
    if parsed.status in {"CLEAN", "NO_DIFF"}:
        return FindingReport(status=parsed.status, findings=[], block_reason=None)
    if parsed.status == "BLOCKED":
        return FindingReport(
            status="BLOCKED",
            findings=[],
            block_reason=parsed.block_reason or "Bugbot review blocked",
        )
    findings = [
        SourceFinding(
            id=None,
            severity=row.severity,
            location=row.location,
            finding=row.finding,
        )
        for row in parsed.findings
    ]
    return FindingReport(status="FINDINGS", findings=findings, block_reason=None)


def project_bug_hunt_review_to_finding_report(
    review: Mapping[str, Any],
) -> FindingReport:
    """Project hunt JSON → ``FindingReport`` только с confirmed production bug."""
    parsed = ProjectBugHuntAnswer.model_validate(dict(review))
    if parsed.status != "HUNT_COMPLETE":
        return FindingReport(
            status="BLOCKED",
            findings=[],
            block_reason=parsed.block_reason or parsed.status,
        )
    repair_rows = [
        row
        for row in parsed.findings
        if row.production_classification == CONFIRMED_PRODUCTION_BUG
    ]
    if not repair_rows:
        return FindingReport(status="CLEAN", findings=[], block_reason=None)
    findings = [
        SourceFinding(
            id=row.finding_id,
            severity="high",
            location=row.loc_link,
            finding=row.literal_claim or row.title,
        )
        for row in repair_rows
    ]
    return FindingReport(status="FINDINGS", findings=findings, block_reason=None)
