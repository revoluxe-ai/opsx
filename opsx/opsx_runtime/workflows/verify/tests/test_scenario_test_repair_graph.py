"""Маршрутизация и узел ``scenario_test_repair`` в verify-subgraph."""

from __future__ import annotations

import pytest

from agents.state import GraphState
from workflows.verify.graph import (
    route_after_scenario_test_repair,
    scenario_test_repair_node,
)
from workflows.verify.green_loop import (
    SCENARIO_TEST_REPAIR_NODE,
    VERIFY_APPLY_COMMIT_NODE,
    VERIFY_APPLY_GAP_SOURCE_SCENARIO,
    patch_verify_shared_apply_loop,
    SCENARIO_COVERAGE_VERIFY_NODE,
)


@pytest.mark.asyncio
async def test_scenario_test_repair_candidate_routes_commit_and_sets_restart():
    class StubRepair:
        async def run(self, state):
            return {
                "scenario_test_repair_status": "CANDIDATE_PENDING_VERIFY",
                "scenario_test_repair_answer": {
                    "summary": "added tests",
                    "files_changed": ["tests/scenario_x.py"],
                },
                "scenario_test_repair_changed_paths": ["tests/scenario_x.py"],
            }

    state: GraphState = {
        "run_uuid": "exec-1",
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
    }
    patch = await scenario_test_repair_node(state, agent=StubRepair())
    assert patch["scenario_test_repair_status"] == "CANDIDATE_PENDING_VERIFY"
    assert patch["verify_finding_fix"]["files_changed"] == ["tests/scenario_x.py"]
    assert patch["verify_scenario_episode_used_repair"] is True
    assert patch["verify_scenario_repair_run_uuid"] == "exec-1"
    assert patch["verify_apply_gap_source"] == VERIFY_APPLY_GAP_SOURCE_SCENARIO
    assert patch["verify_apply_restart_target"] == SCENARIO_COVERAGE_VERIFY_NODE
    merged = {**state, **patch}
    assert route_after_scenario_test_repair(merged) == VERIFY_APPLY_COMMIT_NODE


@pytest.mark.asyncio
async def test_scenario_test_repair_no_change_routes_owner_attention():
    class StubRepair:
        async def run(self, state):
            _ = state
            return {
                "scenario_test_repair_status": "NO_CHANGE",
                "scenario_test_repair_changed_paths": [],
            }

    patch = await scenario_test_repair_node({}, agent=StubRepair())
    assert route_after_scenario_test_repair(patch) == "verify_owner_attention"


def test_shared_apply_loop_patch_scenario_restart():
    loop = patch_verify_shared_apply_loop(
        gap_source=VERIFY_APPLY_GAP_SOURCE_SCENARIO,
        restart_target=SCENARIO_COVERAGE_VERIFY_NODE,
    )
    assert loop["verify_apply_restart_target"] == SCENARIO_COVERAGE_VERIFY_NODE
    assert SCENARIO_TEST_REPAIR_NODE == "scenario_test_repair"
