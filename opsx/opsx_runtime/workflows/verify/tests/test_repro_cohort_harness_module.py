"""repro_cohort HARNESS_MODULE basis."""

from agents.verify.dispatch_unit.repro_cohort import reproduction_cohort


def test_harness_module_cohort() -> None:
    analysis = {
        "analysis_identity": {"change_id": "c", "identity_status": "PARTIAL"},
        "input_integrity": {"status": "COMPLETE", "issues": []},
        "triage_summary": {"findings_total": 1, "finding_groups_total": 0, "ungrouped_total": 1},
        "findings": [
            {
                "finding_id": "F-0001",
                "source_tool": "pytest",
                "repair_workflow": "WF_PYTEST_COLLECTION",
                "category": "TEST_FAILURE",
                "observation": "pytest: SyntaxError in tests/unit/test_x.py",
                "classification": "FACT",
                "evidence_refs": [],
            },
        ],
    }
    cohort = reproduction_cohort(analysis, finding_ids=["F-0001"])
    assert cohort["basis"] == "HARNESS_MODULE"
    assert cohort["test_modules"] == ["tests/unit/test_x.py"]
