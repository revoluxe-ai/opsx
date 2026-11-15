"""Regression: repair subgraph routing — no silent re-discovery on VERIFY_PAUSED."""

from __future__ import annotations

import json
from pathlib import Path

from workflows.repair.schemas import Finding, FindingOutcome, FindingReport, RepairState
from workflows.verify.check_repair_outcome import (
    derive_check_repair_hop_outcome,
    outcome_to_graph_patch,
    route_after_check_repair,
)


def _report() -> FindingReport:
    return FindingReport.model_validate(
        {
            "status": "FINDINGS",
            "findings": [
                {
                    "id": "F-visual",
                    "severity": "medium",
                    "location": "tests/x.py",
                    "finding": "visual bug",
                },
            ],
        },
    )


def test_resource_limit_ineligible_routes_to_end_not_discovery() -> None:
    report = _report()
    task_id = "abc"
    final = RepairState.model_validate(
        {
            "mission_id": "verify-pbh-visual",
            "repository_path": ".",
            "workspace": ".",
            "output_dir": ".",
            "status": "BLOCKED",
            "error": "agent input-token limit exceeded: 5460000>3000000",
            "runs": [],
        },
    )
    outcome = derive_check_repair_hop_outcome(final, report=report, repair_task_id=task_id)
    assert outcome.workflow_outcome == "VERIFY_PAUSED"
    assert outcome.next_allowed == "NONE"
    patch = outcome_to_graph_patch(outcome, state_key_prefix="bug")
    state = {
        "bug_check_repair_workflow_outcome": patch["bug_check_repair_workflow_outcome"],
        "bug_check_repair_next_allowed": patch["bug_check_repair_next_allowed"],
        "verify_workflow_paused": patch["verify_workflow_paused"],
    }
    assert route_after_check_repair(state, "project_bug") == "END"


def test_resource_limit_eligible_address_retry(tmp_path: Path) -> None:
    report = _report()
    ledger = {
        "max_observed_input_per_task": 25_000_000,
        "accumulated_observed_input": 1_000_000,
        "accumulated_estimated_input": 0,
    }
    (tmp_path / "task_budget_ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    final = RepairState.model_validate(
        {
            "mission_id": "verify-pbh-visual",
            "repository_path": ".",
            "workspace": ".",
            "output_dir": str(tmp_path),
            "status": "BLOCKED",
            "error": "RESOURCE_LIMIT",
            "runs": [
                {
                    "hop": "h1",
                    "role": "RED_TEST_WORKER",
                    "execution_id": "m:h1",
                    "state": "BLOCKED",
                    "budget_stop_reason": "RESOURCE_LIMIT",
                },
            ],
        },
    )
    outcome = derive_check_repair_hop_outcome(
        final,
        report=report,
        repair_task_id="task-1",
    )
    assert outcome.next_allowed == "ADDRESS_RETRY"
    state = {
        "bug_check_repair_workflow_outcome": outcome.workflow_outcome,
        "bug_check_repair_next_allowed": outcome.next_allowed,
    }
    assert route_after_check_repair(state, "project_bug") == "project_bug_check_repair"


def test_changed_files_re_verify_goes_discovery() -> None:
    from workflows.repair.schemas import FixEvidence

    report = _report()
    finding = Finding.model_validate(
        {
            "id": "F-visual",
            "finding": "visual bug",
            "location": "tests/x.py",
            "severity": "medium",
        },
    )
    final = RepairState.model_validate(
        {
            "mission_id": "m",
            "repository_path": ".",
            "workspace": ".",
            "output_dir": ".",
            "status": "ACTIVE",
            "finished": [
                FindingOutcome.model_validate(
                    {
                        "finding": finding.model_dump(mode="json"),
                        "terminal_state": "FIXED",
                        "fix": FixEvidence.model_validate(
                            {
                                "status": "FIXED",
                                "files_changed": ["src/x.py"],
                                "summary": "patched",
                            },
                        ),
                    },
                ),
            ],
        },
    )
    outcome = derive_check_repair_hop_outcome(final, report=report, repair_task_id="t")
    assert outcome.next_allowed == "RE_VERIFY"
    state = {
        "bug_check_repair_workflow_outcome": outcome.workflow_outcome,
        "bug_check_repair_next_allowed": outcome.next_allowed,
    }
    assert route_after_check_repair(state, "project_bug") == "project_bug_discovery"
