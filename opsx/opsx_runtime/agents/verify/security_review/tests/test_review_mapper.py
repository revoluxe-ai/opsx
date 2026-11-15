"""Тесты маппинга security review → FindingReport."""

from __future__ import annotations

from agents.verify.security_review.review_mapper import security_review_to_finding_report


def _finding(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "finding_id": "SEC-001",
        "severity": "high",
        "location": "src/auth.py:10",
        "finding": "missing auth check",
        "security_classification": "confirmed security issue",
    }
    base.update(overrides)
    return base


def test_mapper_keeps_confirmed_product_security_only():
    report = security_review_to_finding_report(
        {
            "status": "SECURITY_COMPLETE",
            "mode": "READ_ONLY",
            "confirmed_security_issue_count": 1,
            "findings": [
                _finding(),
                _finding(
                    finding_id="SEC-002",
                    location=".cursor/rules/x.mdc:1",
                    security_classification="process_lane",
                ),
                _finding(
                    finding_id="SEC-003",
                    location="src/other.py:2",
                    security_classification="informational",
                ),
            ],
        },
    )
    assert report.status == "FINDINGS"
    assert len(report.findings) == 1
    assert report.findings[0].id == "SEC-001"


def test_mapper_clean_when_only_process_or_non_confirmed():
    report = security_review_to_finding_report(
        {
            "status": "SECURITY_COMPLETE",
            "mode": "READ_ONLY",
            "confirmed_security_issue_count": 0,
            "findings": [
                _finding(
                    finding_id="SEC-P",
                    location="tools/temporal/config.py:1",
                    security_classification="process_lane",
                ),
            ],
        },
    )
    assert report.status == "CLEAN"
    assert report.findings == []


def test_mapper_drops_confirmed_in_process_path_even_if_misclassified():
    report = security_review_to_finding_report(
        {
            "status": "SECURITY_COMPLETE",
            "mode": "READ_ONLY",
            "confirmed_security_issue_count": 1,
            "findings": [
                _finding(
                    location="opsx/runtime/foo.py:1",
                    security_classification="confirmed security issue",
                ),
            ],
        },
    )
    assert report.status == "CLEAN"
