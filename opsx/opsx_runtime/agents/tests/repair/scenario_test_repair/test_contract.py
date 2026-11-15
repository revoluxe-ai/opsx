"""Pydantic output + authority and filesystem boundary acceptance tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.common.scenario_files import scenario_test_relpath
from agents.repair.scenario_test_repair.agent import (
    validate_candidate, validate_input,
)
from agents.repair.scenario_test_repair.safety import disallowed_paths
from agents.repair.scenario_test_repair.schemas import ScenarioTestRepairAnswer


def _candidate(ids: list[str], changed: str) -> ScenarioTestRepairAnswer:
    return ScenarioTestRepairAnswer(
        status="CANDIDATE_PENDING_VERIFY",
        summary="Test-only candidate; no independent verdict.",
        addressed_gap_ids=ids,
        files_changed=[changed],
        recovery_contract="Run independent ScenarioCoverageVerify on current revision.",
    )


def test_exact_recorded_gaps_are_accepted_as_candidate_not_pass(
    audit_context, repo_root,
):
    ids = [g["scenario_id"] for g in audit_context["scenario_coverage_answer"]["gaps"]]
    paths = [scenario_test_relpath(audit_context["change_id"], sid) for sid in ids]
    answer = ScenarioTestRepairAnswer(
        status="CANDIDATE_PENDING_VERIFY",
        summary="Test-only candidate; no independent verdict.",
        addressed_gap_ids=ids,
        files_changed=paths,
        recovery_contract="Run independent ScenarioCoverageVerify on current revision.",
    )
    assert validate_input(audit_context, repo_root=repo_root, change_id=audit_context["change_id"]) is None
    assert validate_candidate(
        answer, ids, actual_changed=paths, repo=repo_root, change_id=audit_context["change_id"],
    ) is None
    assert answer.status != "SCENARIO_TEST_COVERAGE_PASS"


def test_pydantic_rejects_fabricated_pass_and_incomplete_candidate(audit_context):
    ids = [g["scenario_id"] for g in audit_context["scenario_coverage_answer"]["gaps"]]
    with pytest.raises(ValidationError):
        ScenarioTestRepairAnswer.model_validate({
            "status": "APPLY_COMPLETE", "summary": "claim without gate",
            "addressed_gap_ids": ids, "files_changed": ["tests/a.py"],
            "recovery_contract": "n/a",
        })
    with pytest.raises(ValidationError, match="CANDIDATE requires"):
        ScenarioTestRepairAnswer(
            status="CANDIDATE_PENDING_VERIFY", summary="partial",
            addressed_gap_ids=ids[:1], remaining_gap_ids=ids[1:],
            files_changed=["tests/a.py"], recovery_contract="not complete",
        )


def test_identity_or_authority_mismatch_blocks_before_sdk(audit_context, repo_root):
    for change, expected in (
        ({"scenario_test_repair_authorized": False}, "NOT_AUTHORIZED"),
        ({"isolated_workspace": False}, "NOT_ISOLATED"),
        ({"scenario_coverage_execution_id": "different-run"}, "EXECUTION_STALE"),
        ({"scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS"}, "NOT_SCENARIO_FAIL"),
    ):
        assert expected in str(validate_input(
            {**audit_context, **change},
            repo_root=repo_root, change_id=audit_context["change_id"],
        ))


def test_graph_hop_skips_isolated_workspace_when_audit_bound(audit_context, repo_root):
    bound = {
        **audit_context,
        "scenario_test_repair_graph_hop": True,
        "verify_scenario_repair_audit_run_uuid": audit_context["run_uuid"],
        "isolated_workspace": False,
    }
    assert validate_input(bound, repo_root=repo_root, change_id=audit_context["change_id"]) is None
    stale = {**audit_context, "scenario_coverage_source_snapshot": {"source_digest": "stale"}}
    assert "SOURCE_STALE" in str(validate_input(
        stale, repo_root=repo_root, change_id=audit_context["change_id"],
    ))


def test_unreported_or_out_of_scope_write_cannot_validate(audit_context, repo_root):
    ids = [g["scenario_id"] for g in audit_context["scenario_coverage_answer"]["gaps"]]
    good = scenario_test_relpath(audit_context["change_id"], ids[0])
    assert "FILES_MISMATCH" in str(validate_candidate(
        _candidate(ids, good), ids, actual_changed=[], repo=repo_root, change_id=audit_context["change_id"],
    ))
    unexpected = "src/stroy/notifications/core.py"
    assert "SCOPE_VIOLATION" in str(validate_candidate(
        _candidate(ids, unexpected), ids, actual_changed=[unexpected],
        repo=repo_root, change_id=audit_context["change_id"],
    ))
    assert disallowed_paths([good, "openspec/changes/x/specs/a.md"]) == [
        "openspec/changes/x/specs/a.md"
    ]


def test_missing_gap_partition_fails_closed(audit_context, repo_root):
    ids = [g["scenario_id"] for g in audit_context["scenario_coverage_answer"]["gaps"]]
    path = scenario_test_relpath(audit_context["change_id"], ids[0])
    one = _candidate(ids[:1], path)
    assert "GAP_ACCOUNTING_INVALID" in str(validate_candidate(
        one, ids, actual_changed=one.files_changed, repo=repo_root, change_id=audit_context["change_id"],
    ))


def test_integration_only_candidate_blocked(audit_context, repo_root):
    ids = [g["scenario_id"] for g in audit_context["scenario_coverage_answer"]["gaps"]]
    integration = "tests/integration/notifications/test_pilot_scenario_coverage_pg.py"
    answer = _candidate(ids, integration)
    err = validate_candidate(
        answer, ids, actual_changed=[integration], repo=repo_root, change_id=audit_context["change_id"],
    )
    assert err and "REPAIR_MAPPED_SURFACE_MISSING" in err
