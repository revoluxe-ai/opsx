"""Регрессии parse_pytest_issues и handoff path для pytest findings."""

from __future__ import annotations

from agents.verify.dispatch_unit.repro_cohort import pytest_node_id
from agents.verify.dispatcher.items import finding_item_from_finding
from agents.verify.group_error.schemas import Finding
from agents.verify.test_triage.input_pack import build_test_triage_input_pack
from agents.verify.dispatcher.agent import dispatcher_route_edge
from workflows.verify.gate_repair.diagnostics import (
    extract_gate_diagnostics,
    parse_pytest_issues,
)
from workflows.verify.gate_repair.pytest_gate_timeout import format_pytest_stall_evidence
from workflows.verify.gate_repair.pytest_failure_excerpt import build_pytest_failure_excerpts
from workflows.verify.gate_repair.schemas import GateCheckResult, QualityGateRawBundle


def _pytest_bundle(exit_code: int = 1, log: str = "") -> QualityGateRawBundle:
    stub = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=exit_code,
        duration_ms=1,
        stdout_tail=log[-3500:] if log else "",
    )
    ok = stub.model_copy(update={"exit_code": 0})
    return QualityGateRawBundle(
        pytest=stub,
        pyright=ok.model_copy(update={"check_id": "pyright"}),
        mypy=ok.model_copy(update={"check_id": "mypy"}),
    )


def test_failed_with_e_lines_binds_node_no_orphan_diff() -> None:
    log = """
FAILED tests/scenarios/consultation-funnel-baseline/test_flow.py::test_funnel - AssertionError
E   assert False
E   +  where False = True
E   + pdConsent
    """.strip()
    issues = parse_pytest_issues(log)
    assert len(issues) == 1
    assert "tests/scenarios/consultation-funnel-baseline/test_flow.py::test_funnel" in issues[0]
    assert pytest_node_id(issues[0]) is not None
    assert not any("+ pdConsent" in issue and "::" not in issue for issue in issues)


def test_summary_only_tail_scan() -> None:
    log = """
... lots of dots ...
=========================== short test summary info ============================
FAILED tests/unit/foo/test_bar.py::test_baz - ValueError: bad
    """.strip()
    issues = parse_pytest_issues(log)
    assert len(issues) == 1
    assert issues[0].startswith("pytest: tests/unit/foo/test_bar.py::test_baz")


def test_finding_item_path_from_node_id() -> None:
    finding = Finding(
        finding_id="F-0001",
        source_tool="pytest",
        repair_workflow="WF_PYTEST_FAILURE",
        category="TEST_FAILURE",
        observation="pytest: tests/scenarios/x/test_y.py::test_z - AssertionError: boom",
    )
    item = finding_item_from_finding(finding)
    assert item["path"] == "tests/scenarios/x/test_y.py"
    assert item["line"] is None


def test_extract_gate_diagnostics_mypy_unchanged() -> None:
    mypy_log = "src/stroy/foo.py:10: error: Incompatible types  [arg-type]\nFound 1 error in 1 file"
    bundle = _pytest_bundle(exit_code=0)
    bundle = bundle.model_copy(
        update={
            "mypy": bundle.mypy.model_copy(
                update={"exit_code": 1, "stdout_tail": mypy_log},
            ),
        },
    )
    diag = extract_gate_diagnostics(bundle, mypy_parse_log=mypy_log)
    assert len(diag.mypy) == 1
    assert diag.mypy[0].startswith("src/stroy/foo.py:10:")


def test_failure_excerpt_in_handoff_and_triage_pack() -> None:
    node = "tests/unit/consultation/invest_yalta/test_max_crm_handoff.py::test_human_mode_return_keeps_snapshot"
    log = f"""
=================================== FAILURES ===================================
____________________ test_human_mode_return_keeps_snapshot _____________________

    async def test_human_mode_return_keeps_snapshot(monkeypatch) -> None:
        ...
>       assert back_out.consumed is True and confirm_snap.step == UxState.humanResumeConfirm
E       AssertionError: assert (True is True and <UxState.conf...: 'confirmed'> == <UxState.huma...esumeConfirm'>
E         
E         - humanResumeConfirm
E         + confirmed)

{node}:125: AssertionError
=========================== short test summary info ============================
FAILED {node} - AssertionError: assert ...
    """.strip()
    issues = parse_pytest_issues(log)
    assert issues and node in issues[0]
    excerpts = build_pytest_failure_excerpts(log, [node])
    assert node in excerpts
    assert "humanResumeConfirm" in excerpts[node]
    assert "confirmed" in excerpts[node]

    bundle = _pytest_bundle(exit_code=1, log=log)
    diag = extract_gate_diagnostics(bundle, pytest_parse_log=log)
    assert node in diag.pytest_failure_excerpts
    assert "AssertionError" in diag.pytest_failure_excerpts[node]

    finding = Finding(
        finding_id="F-0008",
        source_tool="pytest",
        repair_workflow="WF_PYTEST_FAILURE",
        category="TEST_FAILURE",
        observation=issues[0],
    )
    item = finding_item_from_finding(
        finding,
        quality_gate_diagnostics=diag.as_handoff_dict(),
    )
    assert item["pytest_failure_excerpt"]
    pack = build_test_triage_input_pack(
        repository_path="/tmp/repo",
        item=item,
        state={"quality_gate_diagnostics": diag.as_handoff_dict()},
    )
    assert pack["pytest_failure_excerpt"]
    assert "humanResumeConfirm" in pack["pytest_failure_excerpt"]


def test_progress_only_stall_routes_to_test_duration() -> None:
    node = (
        "tests/smoke/test_poetry_dev_toolchain_smoke.py"
        "::test_venv_binaries_match_static_check_contract"
    )
    progress_only = "\n".join(
        [
            f"OPSX_GATE_PROGRESS node={node} when=call event=start",
        ]
    )
    check = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=1,
        duration_ms=1,
        error=None,
    )
    bundle = _pytest_bundle(1, progress_only)
    bundle = bundle.model_copy(update={"pytest": check})
    diag = extract_gate_diagnostics(bundle, pytest_parse_log=progress_only)
    item = {
        "finding_id": "F-0001",
        "observation": diag.pytest[0],
        "source_tool": "pytest",
    }
    assert dispatcher_route_edge({"dispatcher_route": "test", "dispatcher_current_item": item}) == (
        "pytest_single_repro"
    )


def test_stall_structured_evidence_routes_to_test_duration() -> None:
    node = "tests/smoke/test_poetry_dev_toolchain_smoke.py::test_venv_binaries_match_static_check_contract"
    evidence = format_pytest_stall_evidence(
        scope="tests",
        stall_limit_s=300.0,
        timeout_s=490.0,
        test_count=1,
        per_test_s=5.0,
        last_node_id=node,
        stdout_tail="OPSX_GATE noise only",
        stderr_tail="",
        stall_when="call",
    )
    check = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=1,
        duration_ms=1,
        error=evidence,
    )
    bundle = _pytest_bundle(1, "progress noise without summary")
    bundle = bundle.model_copy(update={"pytest": check})
    diag = extract_gate_diagnostics(bundle, pytest_parse_log="progress noise")
    assert node in diag.pytest[0]
    item = {
        "finding_id": "F-0001",
        "observation": diag.pytest[0],
    }
    assert dispatcher_route_edge({"dispatcher_route": "test", "dispatcher_current_item": item}) == (
        "pytest_single_repro"
    )


def test_fallback_collection_failed_without_nodes() -> None:
    log = "ERROR collecting tests/broken.py\nSyntaxError: invalid syntax"
    bundle = _pytest_bundle(exit_code=1, log=log)
    diag = extract_gate_diagnostics(bundle, pytest_parse_log=log)
    assert diag.pytest
    assert "collection failed" in diag.pytest[0] or "tests/broken.py" in diag.pytest[0]
