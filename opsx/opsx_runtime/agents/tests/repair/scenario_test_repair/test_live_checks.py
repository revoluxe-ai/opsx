"""Offline tests of live-check selection against the REAL frozen trace matrix."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agents.repair.scenario_test_repair.live_checks import (
    mapped_test_nodes, run_focused_checks,
)
from agents.repair.scenario_test_repair.live_gate import LiveGateBlocked


def test_current_gap_nodes_come_from_frozen_mapping(repo_root, recorded_coverage):
    ids = [g["scenario_id"] for g in recorded_coverage["gaps"]]
    nodes = mapped_test_nodes(repo_root, "external-client-notifications-pilot", ids)
    assert len(nodes) == len(ids) == 2
    assert all(node.startswith("tests/") and "::test_" in node for node in nodes)
    assert any("test_scenario_identity_skip_is_auditable" in node for node in nodes)
    assert any("test_scenario_crm_pilot_does_not_invoke_booking_funnel" in node for node in nodes)


def test_pg_mapped_cases_defer_without_pytest(
    repo_root, recorded_coverage,
):
    ids = [g["scenario_id"] for g in recorded_coverage["gaps"]]
    nodes = mapped_test_nodes(repo_root, "external-client-notifications-pilot", ids)
    needs_pg = any("/integration/" in node or "_pg.py::" in node for node in nodes)
    if not needs_pg:
        pytest.skip("frozen matrix maps gaps to non-integration scenario modules")
    receipt = asyncio.run(
        run_focused_checks(
            repo_root, "external-client-notifications-pilot", ids,
            execution_id="no-live-execution", db_proof_path=None,
        ),
    )
    assert receipt["status"] == "DEFERRED"
    assert receipt.get("exit_code") is None


def test_unmapped_gap_fails_closed_without_fallback_test(repo_root, recorded_coverage):
    actual = [g["scenario_id"] for g in recorded_coverage["gaps"]]
    assert actual
    with pytest.raises(LiveGateBlocked, match="LIVE_TEST_MAPPING_MISSING"):
        mapped_test_nodes(repo_root, "external-client-notifications-pilot", ["missing::scenario"])


@pytest.mark.asyncio
async def test_focused_checks_never_invokes_run_pytest_execute_under_active_loop(
    repo_root, monkeypatch,
) -> None:
    """Регрессия: repair-hop не поднимает pytest даже для unit mapped nodes."""
    ids = ["scenario::offline-regression"]
    monkeypatch.setattr(
        "agents.repair.scenario_test_repair.live_checks.mapped_test_nodes",
        lambda *_args, **_kwargs: ["tests/unit/test_offline.py::test_regression"],
    )
    run_async = AsyncMock()
    monkeypatch.setattr(
        "workflows.verify.pytest_execute.invoke.run_pytest_execute",
        run_async,
    )

    receipt = await run_focused_checks(
        repo_root,
        "external-client-notifications-pilot",
        ids,
        execution_id="regression-no-pytest",
    )

    assert receipt["status"] == "DEFERRED"
    assert receipt.get("exit_code") is None
    assert receipt.get("reason") == "WORKFLOW_REPAIR_PYTEST_SKIPPED"
    run_async.assert_not_awaited()
