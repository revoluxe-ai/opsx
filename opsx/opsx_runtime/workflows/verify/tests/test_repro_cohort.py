"""Regression and acceptance tests for evidence-bound pytest reproduction cohorts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.verify.dispatch_unit.fresh_focused_gates import (
    FreshGateRun,
    observation_matches_fresh_issue,
    run_fresh_gate_for_tool,
)
from agents.verify.dispatch_unit.repro_cohort import (
    failure_fingerprints,
    pytest_node_id,
    reproduction_cohort,
    safe_pytest_modules,
)
from agents.verify.dispatch_unit.verify_group import verify_group_findings
from agents.verify.group_error.schemas import Finding
from workflows.verify.outer_batch import (
    collect_batch_files_changed,
    decide_outer_route_after_unit,
    verify_reset_repair_batch,
)


def _analysis() -> dict:
    return {
        "analysis_identity": {"change_id": "sample", "identity_status": "PARTIAL"},
        "input_integrity": {"status": "COMPLETE"},
        "triage_summary": {"findings_total": 4, "ungrouped_total": 4},
        "findings": [
            {"finding_id": "F-0001", "source_tool": "pytest",
             "observation": "pytest: tests/integration/a.py::test_a"},
            {"finding_id": "F-0002", "source_tool": "pytest",
             "observation": "pytest: tests/integration/a.py::test_b"},
            {"finding_id": "F-0003", "source_tool": "pytest",
             "observation": "pytest: tests/integration/b.py::test_other"},
            {"finding_id": "F-0004", "source_tool": "pytest",
             "observation": "pytest: tests/unit/c.py::test_unit"},
        ],
    }


def test_group_reproduction_includes_shared_fixture_directory() -> None:
    cohort = reproduction_cohort(_analysis(), finding_ids=["F-0001"])
    assert cohort["finding_ids"] == ["F-0001", "F-0002", "F-0003"]
    assert cohort["test_modules"] == ["tests/integration/a.py", "tests/integration/b.py"]
    assert cohort["basis"] == "module_and_directory_fixture_scope"
    assert "F-0004" not in cohort["finding_ids"]


def test_pytest_node_normalized_across_gate_formats() -> None:
    node = "tests/integration/a.py::test_a"
    assert pytest_node_id(f"pytest: {node}") == node
    assert pytest_node_id(f"ERROR {node} - RuntimeError: setup") == node
    assert pytest_node_id(f"pytest: ERROR {node}") == node
    finding = Finding(finding_id="F-0001", source_tool="pytest",
                      observation=f"pytest: {node}")
    assert observation_matches_fresh_issue(
        finding, [f"ERROR {node} - RuntimeError: setup"]
    )


def test_fingerprint_tracks_behavior_not_finding_position() -> None:
    a = failure_fingerprints({"pytest": [
        "pytest: tests/integration/a.py::test_a",
        "pytest: tests/integration/b.py::test_other",
    ]})
    b = failure_fingerprints({"pytest": [
        "pytest: tests/integration/b.py::test_other",
        "pytest: tests/integration/a.py::test_a",
    ]})
    assert a == b


def test_cohort_paths_cannot_escape_tests_directory(tmp_path: Path) -> None:
    integration = tmp_path / "tests" / "integration"
    integration.mkdir(parents=True)
    (integration / "a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    assert safe_pytest_modules(tmp_path, ["tests/integration/a.py"]) == [
        str((integration / "a.py").resolve())
    ]
    with pytest.raises(ValueError, match="unsafe"):
        safe_pytest_modules(tmp_path, ["../production.py"])


def test_focused_pytest_runs_all_cohort_modules_in_single_process(tmp_path, monkeypatch):
    from workflows.verify.gate_repair import runner

    integration = tmp_path / "tests" / "integration"
    integration.mkdir(parents=True)
    for name in ("a.py", "b.py"):
        (integration / name).write_text("def test_a(): pass\n", encoding="utf-8")

    seen = []

    def run_stub(tool, command, *, cwd, timeout_seconds):
        seen.append(list(command))
        return SimpleNamespace(error=None, exit_code=0), "2 passed in 0.2s"

    monkeypatch.setattr(runner, "_run_gate_subprocess", run_stub)
    monkeypatch.setattr(runner, "build_quality_gate_commands", lambda _: {
        "pytest": ["poetry", "run", "python", "-m", "pytest",
                   str(tmp_path / "tests"), "-q", "--tb=short"],
    })
    result = run_fresh_gate_for_tool(
        tmp_path, "pytest", rel_paths=set(),
        pytest_modules=["tests/integration/a.py", "tests/integration/b.py"],
    )
    assert result.ok and result.exit_code == 0
    assert len(seen) == 1
    assert str((integration / "a.py").resolve()) in seen[0]
    assert str((integration / "b.py").resolve()) in seen[0]
    assert str(tmp_path / "tests") not in seen[0]


def test_partial_cohort_failure_cannot_resolve_sibling(monkeypatch, tmp_path):
    from agents.verify.dispatch_unit import verify_group

    monkeypatch.setattr(verify_group, "resolve_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(verify_group, "collect_fresh_runs", lambda *args, **kwargs: {
        "pytest": FreshGateRun(
            tool="pytest", ok=True,
            issues=["ERROR tests/integration/b.py::test_other - fixture failed"],
            detail="exit=1 issues=1", exit_code=1,
        ),
    })
    statuses = verify_group_findings(
        {"repository_path": str(tmp_path), "analysis_result": _analysis()},
        finding_ids=["F-0001"],
        files_changed=["tests/integration/a.py"],
        pytest_modules=["tests/integration/a.py", "tests/integration/b.py"],
    )
    assert len(statuses) == 1
    assert statuses[0].status == "UNKNOWN"


def test_no_change_group_pass_but_full_suite_failure_is_unresolved(monkeypatch, tmp_path):
    from agents.verify.dispatch_unit import verify_group

    monkeypatch.setattr(verify_group, "resolve_repo_root", lambda _: tmp_path)
    monkeypatch.setattr(verify_group, "collect_fresh_runs", lambda *args, **kwargs: {
        "pytest": FreshGateRun(tool="pytest", ok=True, issues=[],
                               detail="cohort green", exit_code=0),
    })
    monkeypatch.setattr(verify_group, "run_fresh_gate_for_tool", lambda *args, **kwargs:
        FreshGateRun(
            tool="pytest", ok=True,
            issues=["ERROR tests/integration/a.py::test_a - setup failure"],
            detail="full gate failed", exit_code=1,
        )
    )
    statuses = verify_group_findings(
        {"repository_path": str(tmp_path), "analysis_result": _analysis()},
        finding_ids=["F-0001"], files_changed=[],
        pytest_modules=["tests/integration/a.py"],
    )
    assert statuses[0].status == "UNRESOLVED"
    assert "cohort passes" in statuses[0].detail


def test_stale_commit_from_previous_batch_not_counted_as_change() -> None:
    assert collect_batch_files_changed({
        "dispatcher_outcomes": [],
        "verify_finding_commit_outcome": {
            "commit_files": ["tests/integration/conftest.py"],
        },
    }) == set()


@pytest.mark.asyncio
async def test_final_gate_must_show_strict_finding_reduction() -> None:
    initial = list(failure_fingerprints({"pytest": [
        "pytest: tests/integration/a.py::test_a",
        "pytest: tests/integration/b.py::test_other",
    ]}))
    unchanged = await verify_reset_repair_batch({
        "verify_outer_batch_start_failures": initial,
        "quality_gate_diagnostics": {"pytest": [
            "pytest: tests/integration/a.py::test_a",
            "pytest: tests/integration/b.py::test_other",
        ]},
        "verify_outer_iteration": 1,
        "verify_outer_max_iterations": 5,
        "verify_cohort_restart_count": 0,
    })
    assert unchanged["verify_loop_status"] == "PAUSE"
    assert unchanged["verify_loop_terminal"] is True
    assert unchanged.get("verify_post_reset_route") is None
    assert unchanged.get("stop_kind") == "COHORT_NO_CONVERGENCE"

    reduced = await verify_reset_repair_batch({
        "verify_outer_batch_start_failures": initial,
        "quality_gate_diagnostics": {"pytest": [
            "pytest: tests/integration/b.py::test_other",
        ]},
        "verify_outer_iteration": 1, "verify_outer_max_iterations": 5,
    })
    assert reduced["verify_loop_status"] == "IN_PROGRESS"
    assert reduced.get("verify_cohort_restart_count") == 0

    regression = await verify_reset_repair_batch({
        "verify_outer_batch_start_failures": initial,
        "quality_gate_diagnostics": {"pytest": [
            "pytest: tests/integration/c.py::test_new",
        ]},
        "verify_outer_iteration": 1,
        "verify_outer_max_iterations": 5,
        "verify_cohort_restart_count": 0,
    })
    assert regression["verify_loop_status"] == "PAUSE"
    assert regression["verify_loop_terminal"] is True
    assert regression.get("verify_post_reset_route") is None
    assert regression.get("stop_kind") == "COHORT_NO_CONVERGENCE"

    exhausted = await verify_reset_repair_batch({
        "verify_outer_batch_start_failures": initial,
        "quality_gate_diagnostics": {},
        "verify_outer_iteration": 2,
        "verify_outer_max_iterations": 5,
        "verify_cohort_restart_count": 3,
        "verify_cohort_max_restarts": 3,
    })
    assert exhausted["verify_loop_status"] == "PAUSE"
    assert exhausted["verify_loop_terminal"] is True
    assert "cohort_restart_exhausted" in str(exhausted.get("owner_attention") or "")


def test_unknown_cohort_member_requires_pause_not_next_unit() -> None:
    decision, patch = decide_outer_route_after_unit({
        "verify_dispatch_mode": "repair_unit",
        "verify_outer_iteration": 1,
        "dispatcher_outcomes": [{
            "outcome": {"verify_results": {
                "member_statuses": [{
                    "finding_id": "F-0001", "status": "UNKNOWN",
                    "detail": "cohort does not reproduce full-suite failure",
                }]
            }}
        }],
    })
    assert decision == "terminal_pause"
    assert "COHORT_VERIFICATION_INCONCLUSIVE" in patch["owner_attention"]


def test_fixture_setup_does_not_grant_destructive_db_authority(monkeypatch) -> None:
    # This OPSX regression runs with opsx/opsx_runtime on sys.path, where
    # `tests` may resolve to a different installed package. Load the *actual*
    # repository helper by source path instead of a potentially shadowed import.
    import runpy

    root = Path(__file__).resolve().parents[5]
    module_path = root / "tests" / "integration" / "db_env_safety.py"
    assert module_path.is_file(), f"missing DB safety helper: {module_path}"
    apply_integration_db_env_safety = runpy.run_path(
        str(module_path)
    )["apply_integration_db_env_safety"]

    monkeypatch.delenv("STROY_ALLOW_DESTRUCTIVE_TEST_DB", raising=False)
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1/stroy_test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1/stroy_app")
    apply_integration_db_env_safety()
    assert "STROY_ALLOW_DESTRUCTIVE_TEST_DB" not in __import__("os").environ
    same = "postgresql+asyncpg://u:p@127.0.0.1/stroy_test"
    monkeypatch.setenv("DATABASE_URL", same)
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL equals DATABASE_URL"):
        apply_integration_db_env_safety()
    assert __import__("os").environ["DATABASE_URL"] == same


@pytest.mark.asyncio
async def test_group_triage_pause_is_not_silent_success(monkeypatch) -> None:
    from agents.verify.dispatch_unit import group_flow

    async def record_stub(state, *, workflow_kind, outcome):
        return {"dispatcher_outcomes": [{"outcome": outcome}]}

    monkeypatch.setattr(group_flow, "dispatcher_record_outcome", record_stub)
    result = await group_flow.finalize_repair_unit_pass({
        "dispatcher_pass_queue": [{
            "item_kind": "repair_unit",
            "repair_unit_key": "F-0001",
            "finding_ids": ["F-0001"],
        }],
        "dispatcher_pass_index": 0,
        "dispatch_unit_triage_result": {
            "status": "PAUSE", "pause_reason": "shared cause uncertain",
        },
    })
    statuses = result["verify_finding_finalize_outcome"]["verify_results"]["member_statuses"]
    assert statuses == [{
        "finding_id": "F-0001",
        "status": "UNKNOWN",
        "detail": "GROUP_TRIAGE_PAUSE: shared cause uncertain",
    }]


def test_oversized_cohort_returns_blocker_not_partial_success() -> None:
    analysis = {
        "analysis_identity": {"change_id": "sample"},
        "input_integrity": {"status": "COMPLETE"},
        "triage_summary": {"findings_total": 65, "ungrouped_total": 65},
        "findings": [{
            "finding_id": f"F-{i:04d}",
            "source_tool": "pytest",
            "observation": f"pytest: tests/integration/t{i}.py::test_case",
        } for i in range(1, 66)],
    }
    cohort = reproduction_cohort(analysis, finding_ids=["F-0001"])
    assert cohort["basis"] == "COHORT_TOO_BROAD"
    assert not cohort["finding_ids"]
    assert "65" in cohort["reason"]


@pytest.mark.asyncio
async def test_test_repair_resume_requires_same_finding_and_gate_evidence(monkeypatch):
    from agents.adapters.abc_adapter import AgentResult
    from agents.verify.test_repair import agent as module
    from agents.verify.test_repair.schemas import TestRepairResult

    class FakeAdapter:
        def __init__(self):
            self.resumes = []

        async def run(self, state, **kwargs):
            self.resumes.append(kwargs["resume_agent_id"])
            answer = TestRepairResult(
                finding_id="F-0001", status="BLOCKED", summary="no edit",
            )
            return AgentResult(
                text=answer.model_dump_json(), status="COMPLETED",
                agent_id="agent-1", run_id="sdk-run-1", parsed=answer,
            )

    monkeypatch.setattr(
        module, "build_test_repair_input_pack",
        lambda **_: {"finding": "stub"},
    )
    fake = FakeAdapter()
    agent = module.TestRepairAgent(fake)
    common = {
        "repository_path": "/repo",
        "workspace": "/repo",
        "change_id": "sample",
        "run_uuid": "exec-1",
        "test_triage_result": {"classification": "TEST"},
        "quality_gate_raw": {"pytest": {"stdout_tail": "FAIL A"}},
    }
    a = await agent.run({
        **common, "dispatcher_current_item": {
            "finding_id": "F-0001", "observation": "pytest: tests/a.py::test_a",
        },
    })
    assert fake.resumes == [None]
    b = await agent.run({
        **common, **a, "dispatcher_current_item": {
            "finding_id": "F-0001", "observation": "pytest: tests/b.py::test_b",
        },
    })
    assert fake.resumes[-1] is None  # F-0001 reused for a different test.
    await agent.run({
        **common, **b, "dispatcher_current_item": {
            "finding_id": "F-0001", "observation": "pytest: tests/b.py::test_b",
        },
    })
    assert fake.resumes[-1] == "agent-1"
