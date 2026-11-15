"""Тесты routing dispatch cycle (static + test paths)."""

from __future__ import annotations

from agents.common.commit_agent import CommitAgent
from agents.repair.fix_bug import FixBugAgent
from agents.state import GraphState
from workflows.repair.graph import build_repair_graph
from workflows.verify.dispatch_cycle.routing import (
    finding_post_repair_route_edge,
    post_commit_route_edge,
    pytest_finding_triage_route_edge,
    pytest_single_repro_route_edge,
    static_triage_route_edge,
    interaction_post_probe_route_edge,
)


def test_static_triage_routes():
    assert static_triage_route_edge({"static_triage_result": {"classification": "LOGIC"}}) == (
        "logic_bug_repair"
    )
    assert static_triage_route_edge({"static_triage_result": {"classification": "TYPING"}}) == (
        "typing_repair"
    )
    assert static_triage_route_edge({"static_triage_result": {"classification": "UNCLEAR"}}) == (
        "static_finding_finalize"
    )


def test_test_triage_routes():
    assert pytest_finding_triage_route_edge({"test_triage_result": {"classification": "LOGIC"}}) == (
        "logic_bug_repair"
    )
    assert pytest_finding_triage_route_edge({"test_triage_result": {"classification": "TEST"}}) == (
        "pytest_execute_pre_repair"
    )
    assert (
        pytest_finding_triage_route_edge(
            {
                "verify_dispatch_mode": "repair_unit",
                "test_triage_result": {"classification": "TEST"},
            },
        )
        == "test_repair"
    )
    assert pytest_finding_triage_route_edge({"test_triage_result": {"classification": "UNCLEAR"}}) == (
        "test_finding_finalize"
    )


def test_post_repair_commit_when_fixed():
    state: GraphState = {
        "verify_dispatch_path": "static",
        "verify_finding_fix": {"status": "FIXED", "files_changed": ["src/a.py"]},
    }
    assert finding_post_repair_route_edge(state) == "finding_commit"


def test_post_repair_batch_recheck_test_path_without_files():
    state: GraphState = {
        "verify_dispatch_path": "test",
        "verify_finding_fix": {"status": "FIXED", "files_changed": []},
    }
    assert finding_post_repair_route_edge(state) == "batch_recheck_handoff"


def test_post_commit_route_by_path():
    assert post_commit_route_edge({"verify_dispatch_path": "test"}) == "test_finding_finalize"
    assert (
        post_commit_route_edge(
            {"verify_dispatch_path": "test", "commit_status": "COMMITTED"},
        )
        == "batch_recheck_handoff"
    )
    assert (
        post_commit_route_edge(
            {
                "verify_dispatch_path": "test",
                "commit_status": "FAILED",
                "verify_finding_commit_outcome": {"error": "git failed"},
            },
        )
        == "test_finding_finalize"
    )
    assert post_commit_route_edge({"verify_dispatch_path": "static"}) == "static_finding_finalize"


def test_single_repro_routes():
    assert (
        pytest_single_repro_route_edge(
            {"pytest_single_repro_result": {"status": "PASS"}},
        )
        == "test_interaction"
    )
    assert (
        pytest_single_repro_route_edge(
            {"pytest_single_repro_result": {"status": "SAME_FAIL"}},
        )
        == "test_triage"
    )


def test_interaction_routes():
    assert (
        interaction_post_probe_route_edge(
            {"test_interaction_result": {"repair_owner": "test_repair"}},
        )
        == "test_repair"
    )


def test_existing_bug_repair_and_commit_entrypoints_importable():
    assert callable(build_repair_graph)
    assert FixBugAgent is not None
    assert CommitAgent is not None
