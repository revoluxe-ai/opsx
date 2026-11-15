"""Тесты маппинга project bug hunt → FindingReport."""

from __future__ import annotations

from agents.verify.project_bug_hunt.review_mapper import (
    bugbot_review_to_finding_report,
    project_bug_hunt_review_to_finding_report,
)


def test_project_hunt_keeps_only_confirmed_production_bug():
    report = project_bug_hunt_review_to_finding_report(
        {
            "status": "HUNT_COMPLETE",
            "mode": "READ_ONLY",
            "confirmed_production_bug_count": 1,
            "findings": [
                {
                    "finding_id": "PBH-001",
                    "title": "bad gate",
                    "loc_link": "src/a.py:10",
                    "production_classification": "confirmed production bug",
                    "literal_claim": "trigger → wrong state",
                    "separate_adjudication_required": True,
                },
                {
                    "finding_id": "PBH-002",
                    "title": "low risk",
                    "loc_link": "src/b.py:2",
                    "production_classification": "reachable but low-risk",
                    "literal_claim": "minor",
                    "separate_adjudication_required": True,
                },
            ],
        },
    )
    assert report.status == "FINDINGS"
    assert len(report.findings) == 1
    assert report.findings[0].id == "PBH-001"
    assert report.findings[0].location == "src/a.py:10"


def test_project_hunt_maps_confirmed_even_without_separate_adjudication():
    report = project_bug_hunt_review_to_finding_report(
        {
            "status": "HUNT_COMPLETE",
            "mode": "READ_ONLY",
            "confirmed_production_bug_count": 1,
            "findings": [
                {
                    "finding_id": "PBH-003",
                    "title": "cover path",
                    "loc_link": "src/x.py:1",
                    "production_classification": "confirmed production bug",
                    "literal_claim": "wrong cover",
                    "separate_adjudication_required": False,
                },
            ],
        },
    )
    assert report.status == "FINDINGS"
    assert len(report.findings) == 1


def test_project_hunt_clean_when_no_confirmed():
    report = project_bug_hunt_review_to_finding_report(
        {
            "status": "HUNT_COMPLETE",
            "mode": "READ_ONLY",
            "confirmed_production_bug_count": 0,
            "findings": [],
        },
    )
    assert report.status == "CLEAN"
    assert report.findings == []


def test_bugbot_mapper_passes_all_findings():
    report = bugbot_review_to_finding_report(
        {
            "status": "FINDINGS",
            "findings": [
                {
                    "severity": "high",
                    "location": "src/x.py:1",
                    "finding": "bug one",
                }
            ],
        },
    )
    assert report.status == "FINDINGS"
    assert len(report.findings) == 1
