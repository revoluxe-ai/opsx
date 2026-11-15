"""Harness findings закрываются по collect_proof без full-suite."""

from agents.verify.dispatch_unit.verify_group import verify_group_findings


def test_harness_resolved_from_scope_cache() -> None:
    state = {
        "repository_path": ".",
        "analysis_result": {
            "analysis_identity": {"identity_status": "PARTIAL"},
            "input_integrity": {"status": "COMPLETE", "issues": []},
            "triage_summary": {"findings_total": 1},
            "findings": [
                {
                    "finding_id": "F-0001",
                    "source_tool": "pytest",
                    "repair_workflow": "WF_PYTEST_COLLECTION",
                    "observation": "pytest: errors during collection tests/unit/test_x.py",
                    "classification": "FACT",
                    "evidence_refs": [],
                },
            ],
        },
        "last_pytest_execute_by_scope": {
            "tests/unit/test_x.py": {
                "collect_ok": True,
                "collect_exit_code": 0,
                "collect_tail": "collected 0 items",
            },
        },
    }
    statuses = verify_group_findings(state, finding_ids=["F-0001"], files_changed=[])
    assert statuses[0].status == "RESOLVED"
