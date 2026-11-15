"""Маппинг SecurityReviewAnswer → FindingReport для bug-team repair."""

from __future__ import annotations

import re
from typing import Any, Mapping

from agents.verify.security_review.schemas import SecurityReviewAnswer
from workflows.repair.schemas import FindingReport, SourceFinding

CONFIRMED_SECURITY_ISSUE = "confirmed security issue"

PRODUCT_PREFIXES: tuple[str, ...] = ("src/", "tests/")

PROCESS_PREFIXES: tuple[str, ...] = (
    ".cursor/",
    ".agents/",
    "opsx/",
    "opsx-knowledge/",
    ".tmp/opsx",
    "tools/",
    "openspec/",
)


def _normalize_location_path(location: str) -> str:
    path = " ".join(location.strip().split()).strip("`")
    match = re.match(r"^(?P<file_path>.+):(?P<line>\d+)$", path)
    if match:
        path = match.group("file_path")
    return path.replace("\\", "/").lower().lstrip("./")


def _is_product_location(location: str) -> bool:
    norm = _normalize_location_path(location)
    return any(norm.startswith(prefix) for prefix in PRODUCT_PREFIXES)


def _is_process_location(location: str) -> bool:
    norm = _normalize_location_path(location)
    return any(norm.startswith(prefix) for prefix in PROCESS_PREFIXES)


def security_review_to_finding_report(review: Mapping[str, Any]) -> FindingReport:
    """Security review JSON → ``FindingReport`` только с confirmed issues в src/tests."""
    parsed = SecurityReviewAnswer.model_validate(dict(review))
    if parsed.status != "SECURITY_COMPLETE":
        return FindingReport(
            status="BLOCKED",
            findings=[],
            block_reason=parsed.block_reason or parsed.status,
        )
    repair_rows = [
        row
        for row in parsed.findings
        if row.security_classification == CONFIRMED_SECURITY_ISSUE
        and _is_product_location(row.location)
        and not _is_process_location(row.location)
    ]
    if not repair_rows:
        return FindingReport(status="CLEAN", findings=[], block_reason=None)
    findings = [
        SourceFinding(
            id=row.finding_id,
            severity=row.severity if row.severity != "informational" else "medium",
            location=row.location,
            finding=row.finding,
        )
        for row in repair_rows
    ]
    return FindingReport(status="FINDINGS", findings=findings, block_reason=None)
