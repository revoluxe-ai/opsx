from __future__ import annotations

import asyncio

import pytest

from workflows.repair.graph import (
    build_bug_team_graph,
    build_repair_graph,
)
from workflows.repair.red_operative_validation import RedOperativeValidation
from workflows.repair.schemas import Finding, RepairState

from .fakes import FakeAdapter, response


def classification(kind="IMPLEMENTATION_BUG"):
    route = {
        "IMPLEMENTATION_BUG": "ROUTE_A",
        "NEW_REQUIREMENT": "ROUTE_NEW_CHANGE",
        "ARCHIVED_PLANNING_BUG": "ROUTE_ARCHIVED_DELTA",
        "SPEC_BUG": "ROUTE_B",
        "SPEC_GAP": "ROUTE_B",
        "FALSE_POSITIVE": "ROUTE_C",
        "OUT_OF_SCOPE": "ROUTE_D",
        "INCONCLUSIVE": "BLOCKED",
    }[kind]
    return response(
        "CLASSIFIER",
        {
            "status": "BLOCKED" if kind == "INCONCLUSIVE" else "PASS",
            "kind": kind,
            "reason": "classified from evidence",
            "evidence": "src/a.py:10",
            "chosen_route": route,
        },
    )


def red(test_id="tests/test_a.py::test_bug"):
    return response(
        "RED_TEST_WORKER",
        {
            "status": "RED_CONFIRMED",
            "test_id": test_id,
            "test_path": "tests/test_a.py",
            "command": f"pytest {test_id}",
            "expected_failure": "expected reply",
            "actual_failure": "reply missing",
            "failure_match": True,
        },
    )


def fix():
    return response(
        "IMPLEMENTATION_EXECUTOR",
        {
            "status": "FIXED",
            "summary": "fixed root cause",
            "files_changed": ["src/a.py"],
            "execution_route": "LOCAL_REPAIR",
            "patch_hash": "patch-1",
        },
    )


def green(test_id="tests/test_a.py::test_bug"):
    return response(
        "GREEN_VERIFIER",
        {
            "status": "GREEN_CONFIRMED",
            "test_id": test_id,
            "command": f"pytest {test_id}",
            "targeted_regressions": [test_id],
        },
    )


def green_blocked(test_id="tests/test_a.py::test_bug"):
    return response(
        "GREEN_VERIFIER",
        {
            "status": "BLOCKED",
            "test_id": test_id,
            "command": f"pytest {test_id}",
            "notes": "run_pytest MCP unavailable",
        },
    )


def invariant():
    return response(
        "INVARIANT_CAPTURE_WORKER",
        {
            "invariant_id": "INV-1",
            "plain_language_invariant": "A consumed update must return its reply.",
            "technical_invariant": (
                "When consumed=True and reply is present, the webhook boundary "
                "must return the reply before marking duplicate suppression."
            ),
            "scope": "Telegram INVEST YALTA webhook/worker seam",
            "entry_conditions": ["consumed update"],
            "required_behavior": ["reply reaches user"],
            "forbidden_behavior": ["drop reply"],
            "known_writers": ["webhook"],
            "known_readers": ["worker"],
            "proof_from_root_finding": "RED failed before fix and GREEN passed after",
            "hunt_scope": ["src/stroy/webhooks", "src/stroy/normalize"],
        },
    )


def hunt_clear():
    return response(
        "BOUNDED_HUNTER",
        {
            "hunt_result": "CLEAR",
            "candidates": [],
            "scope_checked": ["src/stroy"],
            "notes": "no more invariant violations",
        },
    )


def hunt_candidate(candidate_id="HC-2"):
    return response(
        "BOUNDED_HUNTER",
        {
            "hunt_result": "CANDIDATES_FOUND",
            "candidates": [
                {
                    "hunt_candidate_id": candidate_id,
                    "evidence_paths": ["src/b.py"],
                    "observed_behavior": "same reply is dropped",
                    "possible_invariant_violation": "consumed reply is lost",
                    "affected_component": "src/b.py",
                    "confidence": 0.95,
                    "location": "src/b.py:20",
                    "severity": "high",
                }
            ],
            "scope_checked": ["src"],
        },
    )


def same_relation(candidate_id="HC-2"):
    return response(
        "RELATION_CLASSIFIER",
        {
            "hunt_candidate_id": candidate_id,
            "relation": "SAME_INVARIANT_FINDING",
            "reason": "same consumed-reply invariant",
            "blocks_family_closure": True,
        },
    )


def fanin():
    return response(
        "FAN_IN_ASSEMBLER",
        {
            "status": "FANIN_READY",
            "root_bug": "reply dropped",
            "root_cause_pattern": "duplicate suppression before reply delivery",
            "error_class": "consumed reply loss",
            "sibling_variants": [],
            "fix_patterns": ["deliver reply before marking event"],
            "regression_test_pattern": "same RED/GREEN identity",
            "remaining_exceptions": [],
            "blocking_findings": [],
            "branch_summary": [],
        },
    )


def state(tmp_path, **updates):
    base = {
        "mission_id": "dogfood-test",
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "isolated_workspace": True,
        "output_dir": str(tmp_path / "run"),
        "owner_go": True,
        "owner_final": "CLOSE_EPISODE_NO_CLASS_LESSON",
        "max_search_rounds": 0,
    }
    base.update(updates)
    return RepairState(**base)


def invoke(graph, st):
    return RepairState.model_validate(
        asyncio.run(graph.ainvoke(st.model_dump(mode="json")))
    )


def test_single_repair_subgraph_reaches_fixed(tmp_path):
    adapter = FakeAdapter(
        [classification(), red(), fix(), green(), invariant()]
    )
    graph = build_repair_graph(adapter, include_preflight=False)
    st = state(
        tmp_path,
        active=Finding(id="F1", finding="bug", location="src/a.py:10"),
    )
    result = invoke(graph, st)
    assert result.status == "FIXED"
    assert result.finished[0].terminal_state == "FIXED"
    assert result.invariants[0].invariant_id == "INV-1"
    assert len(adapter.calls) == 5


def test_false_positive_never_runs_red_or_fix(tmp_path):
    adapter = FakeAdapter([classification("FALSE_POSITIVE")])
    graph = build_repair_graph(adapter, include_preflight=False)
    st = state(
        tmp_path,
        active=Finding(id="F1", finding="not bug", location="a:1"),
    )
    result = invoke(graph, st)
    assert result.status == "REJECTED_FALSE_POSITIVE"
    assert len(adapter.calls) == 1


def test_spec_gap_raises_opsx_update_stub(tmp_path):
    adapter = FakeAdapter([classification("SPEC_GAP")])
    with pytest.raises(NotImplementedError, match="/opsx-update"):
        invoke(
            build_repair_graph(adapter, include_preflight=False),
            state(
                tmp_path,
                active=Finding(id="F1", finding="spec issue", location="a:1"),
            ),
        )
    assert len(adapter.calls) == 1


def test_green_blocked_finishes_with_terminal_blocked(tmp_path):
    adapter = FakeAdapter([classification(), red(), fix(), green_blocked()])
    result = invoke(
        build_repair_graph(adapter, include_preflight=False),
        state(
            tmp_path,
            active=Finding(id="F1", finding="bug", location="a:1"),
        ),
    )
    assert len(result.finished) == 1
    assert result.finished[0].terminal_state == "BLOCKED"
    assert result.finished[0].finding.id == "F1"
    assert "F1" in result.seen


def test_invalid_green_test_identity_blocks(tmp_path):
    adapter = FakeAdapter(
        [
            classification(),
            red(test_id="tests/t.py::x"),
            fix(),
            green(test_id="tests/t.py::different"),
        ]
    )
    result = invoke(
        build_repair_graph(adapter, include_preflight=False),
        state(
            tmp_path,
            active=Finding(id="F1", finding="bug", location="a:1"),
        ),
    )
    assert result.status == "BLOCKED"
    assert "TEST_ID_MISMATCH" in (result.error or "")


def test_invalid_green_hash_blocks_without_non_weaken_delta(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workflows.repair import red_operative_validation as rov

    monkeypatch.setattr(
        rov,
        "validate_bug_team_red_operative",
        lambda evidence, *, repo_root: RedOperativeValidation(
            ok=True,
            test_content_identity="old-hash",
            errors=[],
        ),
    )
    monkeypatch.setattr(
        "agents.repair.nodes_repair.validate_bug_team_red_operative",
        lambda evidence, *, repo_root: RedOperativeValidation(
            ok=True,
            test_content_identity="old-hash",
            errors=[],
        ),
    )
    monkeypatch.setattr(
        "agents.repair.green_verifier.agent.red_test_file_content_identity",
        lambda _r, _n: "new-hash",
    )
    adapter = FakeAdapter(
        [
            classification(),
            red(),
            fix(),
            green(),
        ]
    )
    result = invoke(
        build_repair_graph(adapter, include_preflight=False),
        state(
            tmp_path,
            active=Finding(id="F1", finding="bug", location="a:1"),
        ),
    )
    assert result.status == "BLOCKED"
    assert "TEST_HASH_CHANGED_WITHOUT_NON_WEAKEN_DELTA" in (result.error or "")


def test_full_bug_team_one_defect_to_fanin(tmp_path):
    adapter = FakeAdapter(
        [
            classification(),
            red(),
            fix(),
            green(),
            invariant(),
            hunt_clear(),
            fanin(),
        ]
    )
    graph = build_bug_team_graph(adapter, include_preflight=False)
    st = state(
        tmp_path,
        source_review={
            "status": "FINDINGS",
            "findings": [
                {
                    "id": "F1",
                    "severity": "high",
                    "location": "src/a.py:10",
                    "finding": "reply dropped",
                }
            ],
        },
    )
    result = invoke(graph, st)
    assert result.status == "COMPLETE_NO_CLASS_LESSON"
    assert result.fanin is not None
    assert result.fanin.status == "FANIN_READY"
    assert [x.finding.id for x in result.finished] == ["F1"]


def test_final_owner_auto_close_when_owner_go_without_owner_final(tmp_path):
    """verify nested: owner_go=True без owner_final — без interrupt, COMPLETE_*."""
    adapter = FakeAdapter(
        [
            classification(),
            red(),
            fix(),
            green(),
            invariant(),
            hunt_clear(),
            fanin(),
        ]
    )
    graph = build_bug_team_graph(adapter, include_preflight=False)
    st = state(
        tmp_path,
        owner_final=None,
        source_review={
            "status": "FINDINGS",
            "findings": [
                {
                    "id": "F1",
                    "severity": "high",
                    "location": "src/a.py:10",
                    "finding": "reply dropped",
                }
            ],
        },
    )
    result = invoke(graph, st)
    assert result.status == "COMPLETE_NO_CLASS_LESSON"
    assert result.owner_final == "CLOSE_EPISODE_NO_CLASS_LESSON"
    assert result.fanin is not None
    assert result.fanin.status == "FANIN_READY"


def test_recursive_two_defect_family_repairs_both(tmp_path):
    adapter = FakeAdapter(
        [
            classification(),
            red("tests/t.py::root"),
            fix(),
            green("tests/t.py::root"),
            invariant(),
            hunt_candidate("HC-2"),
            same_relation("HC-2"),
            classification(),
            red("tests/t.py::sibling"),
            fix(),
            green("tests/t.py::sibling"),
            hunt_clear(),
            fanin(),
        ]
    )
    graph = build_bug_team_graph(adapter, include_preflight=False)
    st = state(
        tmp_path,
        source_review={
            "status": "FINDINGS",
            "findings": [
                {
                    "id": "F1",
                    "severity": "high",
                    "location": "src/a.py:10",
                    "finding": "root reply dropped",
                }
            ],
        },
    )
    result = invoke(graph, st)
    assert result.status == "COMPLETE_NO_CLASS_LESSON"
    assert [x.finding.id for x in result.finished] == ["F1", "HC-2"]
    assert result.seen == ["F1", "HC-2"]
    assert len(result.invariants) == 1
    assert result.hunt_rounds == 2


def test_empty_bugbot_review_finishes_without_agents(tmp_path):
    adapter = FakeAdapter([])
    graph = build_bug_team_graph(adapter, include_preflight=False)
    st = state(
        tmp_path,
        source_review={"status": "CLEAN", "findings": []},
    )
    result = invoke(graph, st)
    assert result.status == "COMPLETE_NO_FINDINGS"
    assert adapter.calls == []


def test_agent_input_cap_blocks_after_observed_overage(tmp_path):
    usage = {
        "input_tokens": 1_500_000,
        "output_tokens": 1,
        "cache_read_tokens": 600_001,
        "cache_write_tokens": 0,
        "reasoning_tokens": None,
        "total_tokens": 2_100_002,
    }
    adapter = FakeAdapter([classification()], usage=usage)
    graph = build_repair_graph(adapter, include_preflight=False)
    result = invoke(
        graph,
        state(
            tmp_path,
            max_agent_input_tokens=2_000_000,
            active=Finding(id="F1", finding="bug", location="a:1"),
        ),
    )
    assert result.status == "BLOCKED"
    assert result.runs[0].input_limit_exceeded is True


def test_invalid_json_from_agent_is_hard_failure(tmp_path):
    adapter = FakeAdapter(["not-json"] * 3)
    result = invoke(
        build_repair_graph(adapter, include_preflight=False),
        state(
            tmp_path,
            active=Finding(id="F1", finding="bug", location="a:1"),
        ),
    )
    assert result.status == "BLOCKED"
    assert "invalid agent JSON" in (result.error or "")



def false_positive_relation(candidate_id="HC-FP"):
    return response(
        "RELATION_CLASSIFIER",
        {
            "hunt_candidate_id": candidate_id,
            "relation": "FALSE_POSITIVE",
            "reason": "evidence shows expected behavior",
            "blocks_family_closure": False,
        },
    )


def test_nonblocking_hunt_candidate_requires_final_clear_rehunt(tmp_path):
    adapter = FakeAdapter(
        [
            classification(),
            red(),
            fix(),
            green(),
            invariant(),
            hunt_candidate("HC-FP"),
            false_positive_relation("HC-FP"),
            hunt_clear(),
            fanin(),
        ]
    )
    result = invoke(
        build_bug_team_graph(adapter, include_preflight=False),
        state(
            tmp_path,
            source_review={
                "status": "FINDINGS",
                "findings": [
                    {
                        "id": "F1",
                        "severity": "high",
                        "location": "src/a.py:10",
                        "finding": "root reply dropped",
                    }
                ],
            },
        ),
    )
    assert result.status == "COMPLETE_NO_CLASS_LESSON"
    assert result.hunt_rounds == 2
    assert len(result.hunts) == 2
    assert result.hunts[-1].hunt_result == "CLEAR"
    assert any(
        item.get("relation") == "FALSE_POSITIVE"
        for item in result.rejects
    )
