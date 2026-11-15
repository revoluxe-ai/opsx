"""Тесты полноты mypy diagnostics, parser и input integrity."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.state import GraphState
from agents.verify.group_error.agent import GroupErrorAgent
from agents.verify.group_error.diagnostics_input_integrity import (
    input_integrity_from_gate_diagnostics,
)
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.verify.repair_order.agent import RepairOrderAgent
from workflows.verify.gate_repair.diagnostics import extract_gate_diagnostics
from workflows.verify.gate_repair.mypy_output import (
    parse_mypy_issues,
    parse_mypy_reported_error_count,
    synthesize_mypy_log,
)
from workflows.verify.gate_repair.runner import _tail, run_deterministic_quality_gates_sync
from workflows.verify.gate_repair.schemas import GateCheckResult, QualityGateRawBundle
from workflows.verify.pytest_execute.schemas import CollectProof, PytestExecuteResult


def _mypy_check(**updates: object) -> GateCheckResult:
    base = GateCheckResult(
        check_id="mypy",
        command=["mypy"],
        exit_code=1,
        duration_ms=1,
    )
    return base.model_copy(update=updates)


def _bundle(mypy: GateCheckResult) -> QualityGateRawBundle:
    stub = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=0,
        duration_ms=1,
    )
    return QualityGateRawBundle(
        pytest=stub,
        pyright=stub.model_copy(update={"check_id": "pyright"}),
        mypy=mypy,
    )


def test_parse_ninety_errors_full_output():
    lines = [
        f"src/stroy/mod_{i}.py:{i}: error: Failure {i}  [arg-type]"
        for i in range(1, 91)
    ]
    log = synthesize_mypy_log(error_lines=lines, reported_count=90)
    issues = parse_mypy_issues(log)
    assert len(issues) == 90
    assert parse_mypy_reported_error_count(log) == 90


def test_tail_truncation_does_not_affect_full_parse():
    lines = [
        f"src/stroy/mod_{i}.py:{i}: error: Failure {i}  [arg-type]"
        for i in range(1, 91)
    ]
    log = synthesize_mypy_log(error_lines=lines, reported_count=90)
    tailed = _tail(log, limit=3500)
    assert len(parse_mypy_issues(log)) == 90
    assert len(parse_mypy_issues(tailed)) < 90


def test_summary_at_end_still_parsed():
    log = (
        "a.py:1: error: one  [x]\n"
        "b.py:2: error: two  [x]\n"
        "Found 2 errors in 2 files"
    )
    assert parse_mypy_reported_error_count(log) == 2
    assert len(parse_mypy_issues(log)) == 2


def test_multiline_error_with_note():
    log = (
        "src/a.py:10: error: Incompatible types in assignment\n"
        "expression has type \"str\", variable has type \"int\"\n"
        "src/a.py:10: note: See https://mypy.readthedocs.io/\n"
        "Found 1 error in 1 file"
    )
    issues = parse_mypy_issues(log)
    assert len(issues) == 1
    assert "Incompatible types" in issues[0]
    assert "expression has type" in issues[0]
    assert "note:" in issues[0]


def test_same_message_different_lines_are_distinct():
    log = (
        "a.py:1: error: same text  [x]\n"
        "a.py:2: error: same text  [x]\n"
        "Found 2 errors in 1 file"
    )
    assert len(parse_mypy_issues(log)) == 2


def test_windows_path_parsed():
    log = (
        r"C:\Users\vkita\proj\src\foo.py:12: error: Name \"x\" is not defined  [name-defined]\n"
        "Found 1 error in 1 file"
    )
    issues = parse_mypy_issues(log)
    assert len(issues) == 1
    assert issues[0].startswith(r"C:\Users\vkita\proj\src\foo.py:12:")


def test_success_mypy_complete_integrity():
    log = "Success: no issues found in 2 source files"
    check = _mypy_check(exit_code=0)
    diag = extract_gate_diagnostics(_bundle(check), mypy_parse_log=log)
    assert diag.mypy == []
    assert diag.input_integrity_status == "COMPLETE"


def test_reported_parsed_mismatch_partial():
    log = synthesize_mypy_log(
        error_lines=["src/stroy/a.py:1: error: only one  [x]"],
        reported_count=90,
    )
    check = _mypy_check(exit_code=1)
    diag = extract_gate_diagnostics(_bundle(check), mypy_parse_log=log)
    assert diag.input_integrity_status == "PARTIAL"
    assert diag.mypy_reported_error_count == 90
    assert len(diag.mypy) == 1


def test_missing_summary_partial_not_complete():
    log = "a.py:1: error: x  [arg-type]"
    check = _mypy_check(exit_code=1)
    diag = extract_gate_diagnostics(_bundle(check), mypy_parse_log=log)
    assert diag.input_integrity_status == "PARTIAL"
    assert any("summary" in issue.lower() for issue in diag.input_integrity_issues)


def test_subprocess_error_invalid():
    check = _mypy_check(exit_code=124, error="timeout")
    diag = extract_gate_diagnostics(_bundle(check), mypy_parse_log="")
    assert diag.input_integrity_status == "INVALID"


def test_handoff_roundtrip_preserves_integrity():
    lines = [f"src/stroy/f{i}.py:{i}: error: e  [x]" for i in range(1, 4)]
    log = synthesize_mypy_log(error_lines=lines)
    check = _mypy_check(exit_code=1)
    diag = extract_gate_diagnostics(_bundle(check), mypy_parse_log=log)
    raw = json.dumps(diag.as_handoff_dict())
    restored = json.loads(raw)
    loaded = input_integrity_from_gate_diagnostics(restored)
    assert loaded.status == "COMPLETE"
    assert loaded.issues == []


def test_mypy_errors_outside_product_scope_complete_integrity():
    """Ошибки вне ``src/stroy/`` не ломают handoff при полном parse+summary."""
    lines = [f"opsx/pkg/m.py:{i}: error: out of scope  [arg-type]" for i in range(1, 4)]
    log = synthesize_mypy_log(error_lines=lines, reported_count=3)
    check = _mypy_check(exit_code=1)
    diag = extract_gate_diagnostics(_bundle(check), mypy_parse_log=log)
    assert diag.mypy == []
    assert diag.mypy_reported_error_count == 3
    assert diag.input_integrity_status == "COMPLETE"


def test_mypy_mixed_scope_complete_integrity():
    """Summary считает все ошибки; in-scope handoff — подмножество → COMPLETE."""
    lines = [
        "src/stroy/a.py:1: error: in scope  [arg-type]",
        "src/stroy/b.py:2: error: in scope  [arg-type]",
        "tests/smoke/foo.py:3: error: infra  [arg-type]",
    ]
    log = synthesize_mypy_log(error_lines=lines, reported_count=3)
    check = _mypy_check(exit_code=1)
    diag = extract_gate_diagnostics(_bundle(check), mypy_parse_log=log)
    assert len(diag.mypy) == 2
    assert diag.mypy_reported_error_count == 3
    assert diag.input_integrity_status == "COMPLETE"


def test_regression_184_reported_all_parsed():
    lines = [
        f"src/stroy/pkg/module_{i}.py:{(i % 200) + 1}: error: Issue {i}  [arg-type]"
        for i in range(184)
    ]
    log = synthesize_mypy_log(error_lines=lines, reported_count=184)
    check = _mypy_check(exit_code=1)
    diag = extract_gate_diagnostics(_bundle(check), mypy_parse_log=log)
    assert len(diag.mypy) == 184
    assert diag.mypy_reported_error_count == 184
    assert diag.input_integrity_status == "COMPLETE"


class _CountingAdapter(ABCAdapter):
    def __init__(self) -> None:
        self.calls = 0

    async def _run_turn(
        self,
        state,
        *,
        prompt,
        tools,
        payload_model=None,
        resume_agent_id: str | None = None,
    ):
        self.calls += 1
        body = GroupErrorResult(
            analysis_identity=AnalysisIdentity(change_id="c"),
            input_integrity=InputIntegrity(status="PARTIAL", issues=["llm attempt"]),
            ungrouped_finding_ids=[],
            triage_summary=TriageSummary(),
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="a",
            run_id="r",
        )


@pytest.mark.asyncio
async def test_group_error_skips_llm_on_partial_integrity():
    adapter = _CountingAdapter()
    agent = GroupErrorAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {
            "pytest": [],
            "pyright": [],
            "mypy": ["a.py:1: err"],
            "input_integrity_status": "PARTIAL",
            "input_integrity_issues": ["mypy mismatch"],
        },
        "quality_gate_raw": {
            "pytest": {"command": [], "exit_code": 0, "duration_ms": 1},
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 1, "duration_ms": 1},
        },
    }
    result = await agent.run(state)
    assert adapter.calls == 0
    assert result["group_error_execution_status"] == "skipped_input_integrity"
    assert result["analysis_result"]["input_integrity"]["status"] == "PARTIAL"


@pytest.mark.asyncio
async def test_group_error_overwrites_llm_input_integrity(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPSX_GROUP_ERROR_LLM", "1")
    adapter = _CountingAdapter()
    agent = GroupErrorAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {
            "pytest": [],
            "pyright": [],
            "mypy": [],
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
        },
        "quality_gate_raw": {
            "pytest": {"command": [], "exit_code": 0, "duration_ms": 1},
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 0, "duration_ms": 1},
        },
    }
    result = await agent.run(state)
    assert adapter.calls == 1
    assert result["analysis_result"]["input_integrity"]["status"] == "COMPLETE"
    assert result["analysis_result"]["input_integrity"]["issues"] == []


@pytest.mark.asyncio
async def test_repair_order_pauses_on_partial():
    class _RoAdapter(ABCAdapter):
        async def _run_turn(self, state, *, prompt, tools, payload_model=None):
            raise AssertionError("LLM should not run")

    agent = RepairOrderAgent(_RoAdapter())
    analysis = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c"),
        input_integrity=InputIntegrity(status="PARTIAL", issues=["mypy incomplete"]),
        ungrouped_finding_ids=["F-0001"],
        findings=[],
        triage_summary=TriageSummary(findings_total=1, ungrouped_total=1),
    )
    state: GraphState = {
        "change_id": "c",
        "repository_path": ".",
        "analysis_result": analysis.model_dump(mode="json"),
    }
    patch = await agent.run(state)
    assert patch["repair_order_result"]["status"] == "PAUSE"
    assert "PARTIAL" in (patch["repair_order_result"]["pause_reason"] or "")


def test_runner_uses_full_mypy_log_for_diagnostics(tmp_path):
    repo = tmp_path
    (repo / "pyrightconfig_strict.json").write_text("{}", encoding="utf-8")
    (repo / "tests").mkdir()
    lines = [f"src/stroy/x{i}.py:{i}: error: E{i}  [x]" for i in range(1, 6)]
    fake_log = synthesize_mypy_log(error_lines=lines, reported_count=5)

    def fake_run(check_id, command, *, cwd, timeout_seconds):
        if check_id == "mypy":
            return (
                GateCheckResult(
                    check_id="mypy",
                    command=command,
                    exit_code=1,
                    duration_ms=1,
                    stdout_tail=_tail(fake_log),
                ),
                fake_log,
            )
        return (
            GateCheckResult(
                check_id=check_id,  # type: ignore[arg-type]
                command=command,
                exit_code=0,
                duration_ms=1,
            ),
            "",
        )

    async def fake_pytest_execute(repo_root, request, *, state=None, harness_repair_agent=None, repair_adapter=None):
        gate, _ = fake_run("pytest", ["p"], cwd=repo_root, timeout_seconds=600)
        return PytestExecuteResult(
            status="PASS",
            scope_key="tests",
            collect_proof=CollectProof(
                scope_key="tests",
                collect_ok=True,
                collect_exit_code=0,
                attempts_used=0,
            ),
            gate_check=gate,
        )

    with patch(
        "workflows.verify.gate_repair.runner.build_quality_gate_commands",
        return_value={"pytest": ["p"], "pyright": ["r"], "mypy": ["m"]},
    ), patch(
        "workflows.verify.gate_repair.runner.run_pytest_execute",
        side_effect=fake_pytest_execute,
    ), patch(
        "workflows.verify.gate_repair.runner._run_gate_subprocess",
        side_effect=fake_run,
    ), patch(
        "workflows.verify.gate_repair.runner.resolve_opsx_repository_root",
        return_value=repo,
    ):
        state_patch = run_deterministic_quality_gates_sync(
            {"repository_path": str(repo), "change_id": "t"},
        )
    diag = state_patch["quality_gate_diagnostics"]
    assert diag["mypy_parsed_issue_count"] == 5
    assert diag["input_integrity_status"] == "COMPLETE"
