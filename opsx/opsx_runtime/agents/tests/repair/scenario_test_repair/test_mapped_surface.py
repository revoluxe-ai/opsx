"""Mapped repair surface gates (peer-only detection, mapped path validation)."""
from __future__ import annotations

from pathlib import Path

from agents.repair.scenario_test_repair.agent import validate_candidate
from agents.repair.scenario_test_repair.mapped_surface import (
    peer_only_edit,
    validate_mapped_surface_gates,
)
from agents.repair.scenario_test_repair.schemas import ScenarioTestRepairAnswer
from agents.common.scenario_files import scenario_test_relpath


CRM_ID = (
    "openspec/changes/external-client-notifications-pilot/specs/"
    "client-notification-core/spec.md::CRM pilot event does not invoke booking funnel notifier"
)
CHANGE = "external-client-notifications-pilot"


def test_peer_only_edit_detected() -> None:
    surfaces = {"tests/scenarios/x/test_a.py"}
    assert peer_only_edit(
        ["tests/integration/notifications/test_pilot_scenario_coverage_pg.py"],
        surfaces,
    )
    assert not peer_only_edit(["tests/scenarios/x/test_a.py"], surfaces)


def test_validate_mapped_surface_rejects_peer_only(repo_root: Path) -> None:
    err = validate_mapped_surface_gates(
        "CANDIDATE_PENDING_VERIFY",
        addressed_ids=[CRM_ID],
        actual_changed=["tests/integration/notifications/test_pilot_scenario_coverage_pg.py"],
        repo=repo_root,
        change_id=CHANGE,
    )
    assert err and (
        "REPAIR_PEER_ONLY_EDIT" in err or "REPAIR_MAPPED_SURFACE_MISSING" in err
    )


def test_validate_candidate_requires_mapped_path(repo_root: Path) -> None:
    canonical = scenario_test_relpath(CHANGE, CRM_ID)
    answer = ScenarioTestRepairAnswer(
        status="CANDIDATE_PENDING_VERIFY",
        summary="candidate",
        addressed_gap_ids=[CRM_ID],
        files_changed=[canonical],
        recovery_contract="verify",
    )
    assert validate_candidate(
        answer,
        [CRM_ID],
        actual_changed=[canonical],
        repo=repo_root,
        change_id=CHANGE,
    ) is None
    integration = "tests/integration/notifications/test_pilot_scenario_coverage_pg.py"
    bad = ScenarioTestRepairAnswer(
        status="CANDIDATE_PENDING_VERIFY",
        summary="candidate",
        addressed_gap_ids=[CRM_ID],
        files_changed=[integration],
        recovery_contract="verify",
    )
    err = validate_candidate(
        bad,
        [CRM_ID],
        actual_changed=[integration],
        repo=repo_root,
        change_id=CHANGE,
    )
    assert err and "REPAIR_MAPPED_SURFACE_MISSING" in err
