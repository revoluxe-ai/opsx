"""Unit-тесты маршрутизации green-loop между под-workflow verify."""

from __future__ import annotations

import pytest

from langgraph.graph import END

from agents.state import GraphState
from workflows.verify.continuation import VerifyContinuation
from workflows.verify.cycle_checks import (
    CYCLE_CHECKS_STATE_KEY,
    CheckName,
    cycle_checks,
    resolve_code_fingerprint,
)
from workflows.verify.green_loop import (
    DEFAULT_VERIFY_GREEN_MAX_RESTARTS,
    IMPL_VERIFY_ENTRY_NODE,
    SCENARIO_COVERAGE_VERIFY_NODE,
    APPLY_NODE,
    PREPARE_IMPL_RESTART_NODE,
    VERIFY_APPLY_COMMIT_NODE,
    ROUTE_IMPL_RESTART,
    ROUTE_PROJECT_BUG,
    ROUTE_SECURITY,
    ROUTE_SUBGRAPH_END,
    is_gate_repair_workflow_green,
    make_route_after_gate_repair_workflow,
    make_route_after_project_bug_workflow,
    patch_establish_verify_check_circle,
    prepare_impl_restart_node,
    resolve_verify_prepare_main_restart_target,
    route_after_security_workflow,
)


def test_impl_verify_entry_node_symbol():
    assert IMPL_VERIFY_ENTRY_NODE == "impl_verify"


def test_gate_repair_green_no_code_change_routes_to_project_bug():
    route = make_route_after_gate_repair_workflow(
        has_project_bug_hunt=True,
        has_security=True,
    )
    state: GraphState = {
        "verify_loop_status": "VERIFY_OK",
        "quality_gate_verdict": "PASS",
        "quality_gate_diagnostics": {"total_count": 0},
        "quality_gates_execution_status": "DETERMINISTIC",
        "verify_workflow_code_changed": False,
    }
    assert route(state) == ROUTE_PROJECT_BUG


def test_gate_repair_green_with_code_change_routes_to_impl_restart():
    route = make_route_after_gate_repair_workflow(
        has_project_bug_hunt=True,
        has_security=True,
    )
    state: GraphState = {
        "verify_loop_status": "VERIFY_OK",
        "quality_gate_verdict": "PASS",
        "quality_gate_diagnostics": {"total_count": 0},
        "quality_gates_execution_status": "DETERMINISTIC",
        "verify_workflow_code_changed": True,
    }
    assert route(state) == ROUTE_IMPL_RESTART


def test_gate_repair_skipped_pass_is_green_without_quality_snapshot():
    state: GraphState = {
        "verify_loop_status": "VERIFY_OK",
        "static_repair_status": "SKIPPED_PASS",
        "static_repair_marker": "✅ STATIC_REPAIR_SKIP",
    }
    assert is_gate_repair_workflow_green(state)


def test_route_after_static_repair_skipped_pass_routes_project_bug():
    from workflows.verify.green_loop import route_after_gate_repair_circle

    state: GraphState = {
        "verify_loop_status": "VERIFY_OK",
        "static_repair_status": "SKIPPED_PASS",
        "verify_workflow_code_changed": False,
    }
    assert route_after_gate_repair_circle(state) == "project_bug"


def test_gate_repair_not_green_routes_subgraph_end_not_restart():
    from workflows.stop_errors import VerifyStageStop

    route = make_route_after_gate_repair_workflow(
        has_project_bug_hunt=True,
        has_security=False,
    )
    state: GraphState = {
        "verify_loop_status": "VERIFY_LOOP_CAP",
        "verify_workflow_code_changed": True,
    }
    with pytest.raises(VerifyStageStop):
        route(state)


def test_static_repair_red_gates_with_code_change_does_not_restart():
    from workflows.stop_errors import VerifyStageStop
    from workflows.verify.green_loop import route_after_gate_repair_circle

    state: GraphState = {
        "verify_loop_status": "IN_PROGRESS",
        "quality_gate_verdict": "FAIL",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {"total_count": 114},
        "verify_workflow_code_changed": True,
    }
    with pytest.raises(VerifyStageStop):
        route_after_gate_repair_circle(state)


def test_static_repair_green_with_code_change_restarts():
    from workflows.verify.green_loop import route_after_gate_repair_circle

    state: GraphState = {
        "verify_loop_status": "VERIFY_OK",
        "quality_gate_verdict": "PASS",
        "quality_gate_diagnostics": {"total_count": 0},
        "quality_gates_execution_status": "DETERMINISTIC",
        "verify_workflow_code_changed": True,
    }
    assert route_after_gate_repair_circle(state) == "verify_prepare_main_restart"


def test_static_repair_cap_with_code_change_does_not_restart():
    from workflows.stop_errors import VerifyStageStop
    from workflows.verify.green_loop import route_after_gate_repair_circle

    state: GraphState = {
        "verify_loop_status": "VERIFY_LOOP_CAP",
        "quality_gate_verdict": "FAIL",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {"total_count": 30},
        "verify_workflow_code_changed": True,
    }
    with pytest.raises(VerifyStageStop):
        route_after_gate_repair_circle(state)


def test_project_bug_not_green_no_restart_subgraph_end():
    from workflows.stop_errors import VerifyStageStop

    route = make_route_after_project_bug_workflow(has_security=True)
    state: GraphState = {
        "project_bug_hunt_status": "HUNT_COMPLETE",
        "review": {
            "status": "HUNT_COMPLETE",
            "findings": [
                {
                    "finding_id": "PBH-1",
                    "production_classification": "confirmed production bug",
                },
            ],
        },
        "verify_workflow_code_changed": False,
    }
    with pytest.raises(VerifyStageStop):
        route(state)


def test_project_bug_green_no_code_change_routes_bugbot():
    from workflows.verify.green_loop import route_after_project_bug_circle

    state: GraphState = {
        "project_bug_hunt_status": "HUNT_COMPLETE",
        "review": {"status": "HUNT_COMPLETE", "findings": []},
        "verify_workflow_code_changed": False,
    }
    assert route_after_project_bug_circle(state) == "bugbot"


def test_project_bug_green_revalidates_bugbot_receipt_even_with_stamp(tmp_path):
    from workflows.verify.green_loop import route_after_project_bug_circle

    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "project_bug_hunt_status": "HUNT_COMPLETE",
        "review": {"status": "HUNT_COMPLETE", "findings": []},
        "verify_workflow_code_changed": False,
        CYCLE_CHECKS_STATE_KEY: {
            "established": {CheckName.BUGBOT.value: head},
        },
    }
    assert route_after_project_bug_circle(state) == "bugbot"


def test_security_green_no_code_change_routes_subgraph_end():
    state: GraphState = {
        "security_review_status": "SECURITY_COMPLETE",
        "review": {"status": "SECURITY_COMPLETE", "findings": []},
        "verify_workflow_code_changed": False,
    }
    assert route_after_security_workflow(state) == ROUTE_SUBGRAPH_END


def test_route_after_archive_verify_go_vs_no_go():
    from workflows.verify.graph import route_after_archive_verify
    from workflows.verify.green_loop import APPLY_NODE

    assert route_after_archive_verify({"mode": "archive", "verdict": "GO"}) == END
    assert route_after_archive_verify({"mode": "archive", "verdict": "NO_GO"}) == APPLY_NODE


def test_after_security_green_routes_finalize_cycle():
    from workflows.verify.pipeline_routing import (
        VERIFY_FINALIZE_NODE,
        route_verify_pipeline_hop,
    )

    state: GraphState = {
        "security_review_status": "SECURITY_COMPLETE",
        "review": {"status": "SECURITY_COMPLETE", "findings": []},
        "verify_workflow_code_changed": False,
    }
    assert route_verify_pipeline_hop(state, "after_security") == VERIFY_FINALIZE_NODE


def test_verify_pipeline_tail_bugbot_infra_security_finalize():
    """Контракт хвоста: bugbot → infra_tests → security → finalize."""
    from workflows.verify.cycle_checks import CheckName
    from workflows.verify.pipeline_routing import (
        VERIFY_FINALIZE_NODE,
        resolve_skip_forward_after,
        route_verify_pipeline_hop,
    )

    assert resolve_skip_forward_after({}, CheckName.BUGBOT) == "infra_tests"
    assert resolve_skip_forward_after({}, CheckName.INFRA_TESTS) == "security"
    assert resolve_skip_forward_after({}, CheckName.SECURITY) == VERIFY_FINALIZE_NODE

    base: GraphState = {"verify_workflow_code_changed": False}
    assert route_verify_pipeline_hop(
        {**base, "review": {"status": "CLEAN", "findings": []}},
        "after_bugbot",
    ) == "infra_tests"
    assert route_verify_pipeline_hop(
        {**base, "infra_tests_status": "PASS_NO_SCOPE"},
        "after_infra_tests",
    ) == "security"
    assert route_verify_pipeline_hop(
        {
            **base,
            "security_review_status": "SECURITY_COMPLETE",
            "review": {"status": "SECURITY_COMPLETE", "findings": []},
        },
        "after_security",
    ) == VERIFY_FINALIZE_NODE


@pytest.mark.asyncio
async def test_prepare_impl_restart_cap_sets_verify_loop_cap():
    patch = await prepare_impl_restart_node(
        {
            "verify_green_restart_count": DEFAULT_VERIFY_GREEN_MAX_RESTARTS,
            "verify_green_max_restarts": DEFAULT_VERIFY_GREEN_MAX_RESTARTS,
        },
    )
    assert patch["verify_loop_status"] == "VERIFY_LOOP_CAP"
    assert patch["verify_continuation"] == VerifyContinuation(next=END).model_dump()


@pytest.mark.asyncio
async def test_prepare_impl_restart_increments_and_sets_impl_route():
    patch = await prepare_impl_restart_node({"verify_green_restart_count": 0})
    assert patch["verify_continuation"] == VerifyContinuation(
        next="verify_prepare_main_restart",
    ).model_dump()
    assert patch["verify_green_restart_count"] == 1


def test_scenario_coverage_fail_without_files_stops():
    from workflows.verify.graph import route_after_scenario_coverage_verify

    state: GraphState = {
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
        "verify_green_restart_count": 0,
    }
    assert route_after_scenario_coverage_verify(state) == "verify_owner_attention"


def test_scenario_coverage_fail_with_files_commits_once():
    from workflows.verify.graph import route_after_scenario_coverage_verify
    from workflows.verify.green_loop import VERIFY_APPLY_COMMIT_NODE

    state: GraphState = {
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
        "scenario_row_commit_paths": ["tests/scenarios/demo/test_abc.py"],
        "verify_green_restart_count": 0,
    }
    assert route_after_scenario_coverage_verify(state) == VERIFY_APPLY_COMMIT_NODE


def test_scenario_coverage_fail_routes_owner_attention_at_cap():
    from workflows.verify.graph import route_after_scenario_coverage_verify

    state: GraphState = {
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
        "verify_green_restart_count": DEFAULT_VERIFY_GREEN_MAX_RESTARTS,
        "verify_green_max_restarts": DEFAULT_VERIFY_GREEN_MAX_RESTARTS,
    }
    assert route_after_scenario_coverage_verify(state) == "verify_owner_attention"




def test_scenario_fail_after_row_walk_does_not_batch_repair():
    from workflows.verify.graph import route_after_scenario_coverage_verify

    state: GraphState = {
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
        "verify_scenario_episode_used_repair": True,
        "verify_scenario_repair_run_uuid": "run-a",
        "run_uuid": "run-b",
        "verify_green_restart_count": 1,
    }
    assert route_after_scenario_coverage_verify(state) == "verify_owner_attention"


@pytest.mark.asyncio
async def test_apply_active_cursor_run_conflict_pauses_without_commit():
    from agents.adapters.abc_adapter import ActiveCursorAgentRunError
    from workflows.verify.graph import apply_node, route_after_apply
    from workflows.verify.green_loop import verify_owner_attention_stop_node

    class BusyApplyAgent:
        def __init__(self):
            self.calls = 0

        async def run(self, state):
            self.calls += 1
            raise ActiveCursorAgentRunError("agent-busy")

    agent = BusyApplyAgent()
    state: GraphState = {
        "change_id": "example-change",
        "run_uuid": "exec-123",
        "verdict": "IMPL_GAPS",
    }
    patch = await apply_node(state, agent=agent)
    assert agent.calls == 1
    assert patch["apply_status"] == "BLOCKED"
    assert patch["apply_answer"]["files_changed"] == []
    assert patch["verify_finding_fix"] is None
    assert patch["verify_apply_gap_source"] == "impl_verify"
    assert "SDK_ACTIVE_RUN_CONFLICT" in patch["verify_apply_block_reason"]
    assert "agent-busy" in patch["verify_apply_block_reason"]
    assert route_after_apply({**state, **patch}) == "verify_owner_attention"

    stopped = await verify_owner_attention_stop_node({**state, **patch})
    assert "SDK_ACTIVE_RUN_CONFLICT" in stopped["owner_attention"]
    assert stopped["verify_stop_reason"] == "Owner Attention"


@pytest.mark.asyncio
async def test_apply_does_not_mask_unrelated_sdk_failures():
    from workflows.verify.graph import apply_node

    class OtherErrorApplyAgent:
        async def run(self, state):
            raise RuntimeError("Cursor transport not authorized")

    with pytest.raises(RuntimeError, match="Cursor transport not authorized"):
        await apply_node(
            {"scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL"},
            agent=OtherErrorApplyAgent(),
        )


def test_apply_blocked_or_no_change_does_not_commit():
    from workflows.verify.graph import route_after_apply

    for status in ("BLOCKED", "NO_CHANGE"):
        state: GraphState = {
            "apply_status": status,
            "apply_answer": {"files_changed": []},
        }
        assert route_after_apply(state) == "verify_owner_attention"


def test_scenario_partial_commits_then_stops_without_retry():
    from workflows.verify.graph import (
        route_after_prepare_impl_restart,
        route_after_scenario_test_repair,
    )

    state: GraphState = {
        "scenario_test_repair_status": "CANDIDATE_PENDING_VERIFY",
        "scenario_test_repair_changed_paths": ["tests/test_example.py"],
        "verify_apply_gap_source": "scenario_coverage_verify",
    }
    assert route_after_scenario_test_repair(state) == VERIFY_APPLY_COMMIT_NODE
    committed: GraphState = {
        **state,
        "scenario_test_repair_status": "PARTIAL",
    }
    assert route_after_prepare_impl_restart(committed) == "verify_owner_attention"
    state["scenario_test_repair_status"] = "CANDIDATE_PENDING_VERIFY"
    assert route_after_prepare_impl_restart(state) == "verify_prepare_main_restart"


def test_scenario_repair_restart_ignores_stale_apply_partial_status():
    """После scenario repair commit не блокировать re-audit старым ``apply_status``."""
    from workflows.verify.graph import route_after_prepare_impl_restart

    state: GraphState = {
        "scenario_test_repair_status": "CANDIDATE_PENDING_VERIFY",
        "scenario_test_repair_changed_paths": ["tests/test_example.py"],
        "verify_apply_gap_source": "scenario_coverage_verify",
        "apply_status": "APPLY_PARTIAL",
        "apply_answer": {"files_changed": ["tests/legacy.py"]},
    }
    assert route_after_prepare_impl_restart(state) == "verify_prepare_main_restart"


def test_settled_scenario_walk_ignores_stale_apply_status():
    """Итог построчного обхода не зависит от старого ``apply_status``."""
    from workflows.verify.graph import route_after_prepare_impl_restart

    failed: GraphState = {
        "scenario_row_walk_settled": True,
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
        "verify_apply_gap_source": "scenario_coverage_verify",
        "apply_status": "APPLY_PARTIAL",
    }
    assert route_after_prepare_impl_restart(failed) == "verify_owner_attention"
    passed: GraphState = {
        **failed,
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
    }
    assert route_after_prepare_impl_restart(passed) == "coverage_activation"


@pytest.mark.asyncio
async def test_scenario_coverage_unknown_routes_owner_attention_with_original_block_reason():
    from workflows.verify.graph import route_after_scenario_coverage_verify
    from workflows.verify.green_loop import verify_owner_attention_stop_node

    state: GraphState = {
        "verdict": "IMPL_OK",
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_UNKNOWN",
        "scenario_coverage_answer": {
            "block_reason": "AUDIT_NON_CONVERGENT: NO_SOURCE_CHANGE",
            "summary": "Stopped after identical unresolved gaps",
            "gaps": [],
        },
    }
    assert route_after_scenario_coverage_verify(state) == "verify_owner_attention"
    stopped = await verify_owner_attention_stop_node(state)
    assert stopped["verify_stop_reason"] == "Owner Attention"
    assert stopped["owner_attention"] == "AUDIT_NON_CONVERGENT: NO_SOURCE_CHANGE"
    assert stopped["verify_continuation"]["next"] == "__end__"


def test_resolve_verify_prepare_main_restart_scenario_loop():
    state: GraphState = {
        "verify_apply_restart_target": SCENARIO_COVERAGE_VERIFY_NODE,
    }
    assert resolve_verify_prepare_main_restart_target(state) == SCENARIO_COVERAGE_VERIFY_NODE


def test_scenario_green_after_code_change_invalidates_impl_keep_scenario(tmp_path):
    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "verify_check_circle_code_changed": True,
        CYCLE_CHECKS_STATE_KEY: {
            "established": {
                CheckName.IMPL_VERIFY.value: head,
                CheckName.SCENARIO_COVERAGE.value: "old-sha",
            },
        },
    }
    patch = patch_establish_verify_check_circle(state, CheckName.SCENARIO_COVERAGE)
    established = patch[CYCLE_CHECKS_STATE_KEY]["established"]
    assert established[CheckName.SCENARIO_COVERAGE.value] == head
    assert CheckName.IMPL_VERIFY.value not in established


def test_impl_green_after_code_change_invalidates_scenario_keep_impl(tmp_path):
    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "verify_check_circle_code_changed": True,
        CYCLE_CHECKS_STATE_KEY: {
            "established": {
                CheckName.SCENARIO_COVERAGE.value: head,
            },
        },
    }
    patch = patch_establish_verify_check_circle(state, CheckName.IMPL_VERIFY)
    established = patch[CYCLE_CHECKS_STATE_KEY]["established"]
    assert established[CheckName.IMPL_VERIFY.value] == head
    assert CheckName.SCENARIO_COVERAGE.value not in established


def test_scenario_coverage_pass_routes_impl_when_impl_not_established(tmp_path):
    from workflows.verify.graph import route_after_scenario_coverage_verify

    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
        CYCLE_CHECKS_STATE_KEY: {
            "established": {CheckName.SCENARIO_COVERAGE.value: head},
        },
    }
    assert route_after_scenario_coverage_verify(state) == IMPL_VERIFY_ENTRY_NODE


def test_scenario_coverage_pass_routes_coverage_when_impl_still_established(tmp_path):
    from workflows.verify.graph import route_after_scenario_coverage_verify

    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
        CYCLE_CHECKS_STATE_KEY: {
            "established": {
                CheckName.SCENARIO_COVERAGE.value: head,
                CheckName.IMPL_VERIFY.value: head,
            },
        },
    }
    assert route_after_scenario_coverage_verify(state) == "coverage_activation"


def test_impl_verify_impl_ok_routes_scenario_when_scenario_not_established(tmp_path):
    from workflows.verify.graph import route_after_impl_verify

    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "verdict": "IMPL_OK",
        CYCLE_CHECKS_STATE_KEY: {
            "established": {CheckName.IMPL_VERIFY.value: head},
        },
    }
    assert route_after_impl_verify(state) == SCENARIO_COVERAGE_VERIFY_NODE


def test_scenario_coverage_pass_without_apply_routes_coverage_activation(tmp_path):
    from workflows.verify.graph import route_after_scenario_coverage_verify

    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
        CYCLE_CHECKS_STATE_KEY: {
            "established": {
                CheckName.SCENARIO_COVERAGE.value: head,
                CheckName.IMPL_VERIFY.value: head,
            },
        },
    }
    assert route_after_scenario_coverage_verify(state) == "coverage_activation"


def test_scenario_coverage_pass_after_code_change_routes_impl_verify(tmp_path):
    from workflows.verify.graph import route_after_scenario_coverage_verify

    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
        CYCLE_CHECKS_STATE_KEY: {
            "established": {CheckName.SCENARIO_COVERAGE.value: head},
        },
    }
    assert route_after_scenario_coverage_verify(state) == IMPL_VERIFY_ENTRY_NODE


def test_green_circle_without_code_change_preserves_other_stamps(tmp_path):
    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "verify_check_circle_code_changed": False,
        CYCLE_CHECKS_STATE_KEY: {
            "established": {
                CheckName.SCENARIO_COVERAGE.value: head,
                CheckName.FULL_VERIFY_CYCLE.value: head,
            },
        },
    }
    patch = patch_establish_verify_check_circle(state, CheckName.IMPL_VERIFY)
    established = patch[CYCLE_CHECKS_STATE_KEY]["established"]
    assert established[CheckName.IMPL_VERIFY.value] == head
    assert established[CheckName.SCENARIO_COVERAGE.value] == head
    assert established[CheckName.FULL_VERIFY_CYCLE.value] == head


def test_impl_verify_impl_ok_revalidates_receipt_even_with_green_checkpoint(tmp_path):
    from workflows.verify.graph import route_after_impl_verify

    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "verdict": "IMPL_OK",
        CYCLE_CHECKS_STATE_KEY: {
            "established": {CheckName.SCENARIO_COVERAGE.value: head},
        },
    }
    assert route_after_impl_verify(state) == SCENARIO_COVERAGE_VERIFY_NODE


def test_impl_verify_impl_gaps_routes_apply():
    from workflows.verify.graph import route_after_impl_verify

    state: GraphState = {"verdict": "IMPL_GAPS", "verify_green_restart_count": 0}
    assert route_after_impl_verify(state) == APPLY_NODE


def test_resolve_verify_prepare_main_restart_impl_apply_loop():
    state: GraphState = {"verify_apply_restart_target": IMPL_VERIFY_ENTRY_NODE}
    assert resolve_verify_prepare_main_restart_target(state) == IMPL_VERIFY_ENTRY_NODE


def test_resolve_verify_prepare_main_restart_defaults_impl_entry():
    assert resolve_verify_prepare_main_restart_target({}) == IMPL_VERIFY_ENTRY_NODE


def test_verify_graph_declares_impl_first_two_node_apply_loops():
    from workflows.verify.graph import build_verify_graph
    from workflows.verify.tests.test_impl_graph import _stub_verify_agents

    graph = build_verify_graph(*_stub_verify_agents())
    edges = graph.get_graph().edges
    gate_out = [edge for edge in edges if edge.source == "verify_cycle_gate"]
    assert any(edge.target == IMPL_VERIFY_ENTRY_NODE for edge in gate_out)
    assert not any(
        edge.source == "verify_cycle_gate" and edge.target == SCENARIO_COVERAGE_VERIFY_NODE
        for edge in edges
    )
    impl_out = [edge for edge in edges if edge.source == "impl_verify"]
    assert any(edge.target == SCENARIO_COVERAGE_VERIFY_NODE for edge in impl_out)
    assert any(edge.target == "coverage_activation" for edge in impl_out)
    assert any(edge.target == APPLY_NODE for edge in impl_out)
    assert any(edge.target == "verify_owner_attention" for edge in impl_out)
    sc_out = [edge for edge in edges if edge.source == "scenario_coverage_verify"]
    assert any(edge.target == IMPL_VERIFY_ENTRY_NODE for edge in sc_out)
    assert any(edge.target == "coverage_activation" for edge in sc_out)
    from workflows.verify.green_loop import VERIFY_APPLY_COMMIT_NODE

    assert any(edge.target == VERIFY_APPLY_COMMIT_NODE for edge in sc_out)
    assert not any(edge.target == "scenario_test_repair" for edge in sc_out)
    assert not any(
        edge.source == "scenario_coverage_verify" and edge.target == APPLY_NODE for edge in edges
    )
    assert any(edge.target == "verify_owner_attention" for edge in sc_out)
    from workflows.verify.graph import route_after_scenario_test_repair

    assert route_after_scenario_test_repair({
        "scenario_test_repair_status": "CANDIDATE_PENDING_VERIFY",
        "scenario_test_repair_changed_paths": ["tests/scenarios/demo/test_a.py"],
    }) == VERIFY_APPLY_COMMIT_NODE
    assert route_after_scenario_test_repair({
        "scenario_test_repair_status": "BLOCKED",
        "scenario_test_repair_changed_paths": [],
    }) == "verify_owner_attention"
    assert not any(edge.target == "scenario_coverage_terminal" for edge in sc_out)
    ca_out = [edge for edge in edges if edge.source == "coverage_activation"]
    assert any(edge.target == "static_repair" for edge in ca_out)
    assert not any(edge.target == "coverage_activation_terminal" for edge in ca_out)
    assert not any(edge.target == "static_repair" for edge in impl_out)
    assert any(
        edge.source == APPLY_NODE and edge.target == VERIFY_APPLY_COMMIT_NODE for edge in edges
    )
    assert any(
        edge.source == APPLY_NODE and edge.target == "verify_owner_attention" for edge in edges
    )
    assert any(
        edge.source == VERIFY_APPLY_COMMIT_NODE and edge.target == PREPARE_IMPL_RESTART_NODE
        for edge in edges
    )
    assert any(
        edge.source == "verify_prepare_main_restart"
        and edge.target == SCENARIO_COVERAGE_VERIFY_NODE
        for edge in edges
    )
    assert any(
        edge.source == "verify_prepare_main_restart"
        and edge.target == IMPL_VERIFY_ENTRY_NODE
        for edge in edges
    )
    assert not any(
        edge.source == APPLY_NODE and edge.target == "impl_verify" for edge in edges
    )
    assert not any(
        edge.source == VERIFY_APPLY_COMMIT_NODE and edge.target == "verify_owner_attention"
        for edge in edges
    )


def _init_git_repo(path) -> str:
    import subprocess

    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    src = path / "src"
    src.mkdir()
    marker = src / "product.py"
    marker.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    digest = resolve_code_fingerprint({"repository_path": str(path)})
    assert digest is not None
    return digest


def test_cycle_checks_establish_returns_nested_patch(tmp_path):
    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
    }
    patch = cycle_checks(state).establish(CheckName.FULL_VERIFY_CYCLE)
    assert patch == {
        CYCLE_CHECKS_STATE_KEY: {
            "established": {CheckName.FULL_VERIFY_CYCLE.value: head},
        },
    }


def test_cycle_checks_invalidate_empty_when_code_unchanged(tmp_path):
    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        CYCLE_CHECKS_STATE_KEY: {"established": {CheckName.FULL_VERIFY_CYCLE.value: head}},
    }
    assert cycle_checks(state).invalidate() == {}


def test_cycle_checks_invalidate_clears_when_product_content_changes(tmp_path):
    head = _init_git_repo(tmp_path)
    state: GraphState = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        CYCLE_CHECKS_STATE_KEY: {"established": {CheckName.FULL_VERIFY_CYCLE.value: head}},
    }
    # A real product modification invalidates immediately, even before commit.
    (tmp_path / "src/product.py").write_text("x = 2\n", encoding="utf-8")
    patch = cycle_checks(state).invalidate()
    assert patch == {CYCLE_CHECKS_STATE_KEY: {"established": {}}}


def test_scenario_audit_requires_all_rows_bound_to_receipt():
    from agents.verify.scenario_coverage_verify.agent import _audit_completeness_error
    from agents.verify.scenario_coverage_verify.schemas import (
        ScenarioCoverageRowReview,
        ScenarioCoverageVerifyAnswer,
    )

    plan_bundle = {
        "plan_text": (
            "SCENARIO_COVERAGE_PLAN:\n"
            "  SCENARIOS:\n"
            "    - SCENARIO_ID: path::A\n"
            "    - SCENARIO_ID: path::B\n"
        ),
        "plan_receipt": {"PLAN_EVIDENCE_ID": "receipt-a"},
    }
    # This test isolates frozen-plan row/receipt completeness. Source-line
    # semantics are validated separately by test_scenario_audit_evidence.py.
    # Supply schema-valid synthetic proof references and source identity here.
    one = ScenarioCoverageRowReview(
        scenario_id="path::A", test_node_id="tests/a.py::test_a",
        assessment="SUPPORTED", evidence="tests/a.py::test_a asserts case A",
        when_proofs=[1], outcome_proofs=[2],
    )
    two = ScenarioCoverageRowReview(
        scenario_id="path::B", test_node_id="tests/b.py::test_b",
        assessment="SUPPORTED", evidence="tests/b.py::test_b asserts case B",
        when_proofs=[1], outcome_proofs=[2],
    )
    answer = ScenarioCoverageVerifyAnswer(
        verdict="SCENARIO_TEST_COVERAGE_PASS",
        summary="All mapped behavior reviewed",
        gaps=[],
        local_hop_copy_block="audit",
        plan_evidence_id="receipt-a",
        source_digest="synthetic-source-digest-for-completeness-test",
        row_reviews=[one, two],
    )
    assert _audit_completeness_error(answer, plan_bundle) is None
    assert "AUDIT_INCOMPLETE" in _audit_completeness_error(
        answer.model_copy(update={"row_reviews": [one]}), plan_bundle
    )
    assert "AUDIT_STALE" in _audit_completeness_error(
        answer.model_copy(update={"plan_evidence_id": "old-receipt"}), plan_bundle
    )



def test_routing_fingerprint_ignores_git_history_and_opsx(tmp_path):
    import subprocess

    original = _init_git_repo(tmp_path)
    assert original == resolve_code_fingerprint({"repository_path": str(tmp_path)})
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "metadata only"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    assert resolve_code_fingerprint({"repository_path": str(tmp_path)}) == original
    opsx = tmp_path / "opsx/opsx_runtime/workflows/verify/graph.py"
    opsx.parent.mkdir(parents=True)
    opsx.write_text("changed route\\n", encoding="utf-8")
    test = tmp_path / "tests/opsx/test_harness.py"
    test.parent.mkdir(parents=True)
    test.write_text("changed harness\\n", encoding="utf-8")
    assert resolve_code_fingerprint({"repository_path": str(tmp_path)}) == original
    (tmp_path / "src/product.py").write_text("x = 2\\n", encoding="utf-8")
    assert resolve_code_fingerprint({"repository_path": str(tmp_path)}) != original
