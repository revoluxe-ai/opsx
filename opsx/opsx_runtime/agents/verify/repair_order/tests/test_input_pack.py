"""Тесты сборки repair-order input package."""

from __future__ import annotations

from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    FindingGroup,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.verify.repair_order.input_pack import build_repair_order_package


def test_package_exposes_lexicographic_sort_and_input_order():
    analysis = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1"),
        input_integrity=InputIntegrity(status="COMPLETE"),
        finding_groups=[
            FindingGroup(
                group_id="TG-001",
                finding_ids=["F-0001"],
                matched_problem="p",
                match_basis="b",
                diagnostic_sources=["pytest", "pyright"],
            ),
        ],
        ungrouped_finding_ids=["F-0002"],
        findings=[
            Finding(
                finding_id="F-0001",
                source_tool="pytest",
                repair_workflow="WF_PYTEST_FAILURE",
                observation="src/a.py:10",
            ),
            Finding(
                finding_id="F-0002",
                source_tool="mypy",
                repair_workflow="WF_MYPY_TYPE",
            ),
        ],
        triage_summary=TriageSummary(
            findings_total=2,
            finding_groups_total=1,
            ungrouped_total=1,
        ),
    )
    package = build_repair_order_package(normalize_group_error_result(analysis))
    rules = package["sorting_rules"]
    keys = [row["criterion"] for row in rules["lexicographic_sort"]]
    assert keys == [
        "EXPLICIT_DEPENDENCY",
        "FINDING_DEFINITENESS",
        "POTENTIAL_EFFECT",
        "LIMITED_SCOPE",
        "STABLE_TIE_BREAK",
    ]
    assert rules["input_order"] == [
        {"unit_type": "GROUP", "unit_id": "TG-001"},
        {"unit_type": "GROUP", "unit_id": "F-0002"},
    ]
    assert "grouped_errors" in package
    obs_by_id = {f["finding_id"]: f.get("observation") for f in package["findings"]}
    assert obs_by_id["F-0001"] == "src/a.py:10"
