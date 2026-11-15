"""Регрессия: pytest FAIL с распарсенными node id → input_integrity COMPLETE."""

from __future__ import annotations

from agents.verify.group_error.diagnostics_input_integrity import (
    input_integrity_from_gate_diagnostics,
)
from workflows.verify.gate_repair.diagnostics import (
    extract_gate_diagnostics,
    pytest_observations_from_structured_gate_error,
)
from workflows.verify.gate_repair.pytest_gate_timeout import format_pytest_stall_evidence
from workflows.verify.gate_repair.input_integrity import (
    assess_pytest_input_integrity,
    is_pytest_structured_gate_evidence,
    merge_input_integrity_status,
    promote_integrity_when_pytest_nodes_present,
    reconcile_handoff_input_integrity,
)
from workflows.verify.gate_repair.pytest_gate_timeout import format_pytest_timeout_evidence
from workflows.verify.gate_repair.schemas import GateCheckResult, QualityGateRawBundle


def _bundle(pytest_exit: int, pytest_log: str) -> QualityGateRawBundle:
    stub = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=pytest_exit,
        duration_ms=1,
        stdout_tail=pytest_log[-3500:] if pytest_log else "",
    )
    ok = stub.model_copy(update={"exit_code": 0})
    mypy_ok = ok.model_copy(
        update={
            "check_id": "mypy",
            "stdout_tail": "Success: no issues found in 1 source file",
        },
    )
    return QualityGateRawBundle(
        pytest=stub,
        pyright=ok.model_copy(update={"check_id": "pyright"}),
        mypy=mypy_ok,
    )


def test_pytest_failure_with_nodes_is_complete_integrity() -> None:
    node = (
        "tests/scenarios/consultation-funnel-baseline/"
        "test_79afd42ddc6ae8611815d140237a34708caa04ba06a785b23cd36d0b1b20be19.py"
        "::test_export_and_pass"
    )
    log = (
        f"FAILED {node} - tools.openspec.consultation_baseline.errors."
        "PinnedSourceLoadError: byte pin sha256 mismatch\n"
        "E   AssertionError: ux_state_contract.json"
    )
    bundle = _bundle(1, log)
    diag = extract_gate_diagnostics(bundle, pytest_parse_log=log)
    assert diag.input_integrity_status == "COMPLETE"
    assert diag.pytest
    loaded = input_integrity_from_gate_diagnostics(diag.as_handoff_dict())
    assert loaded.status == "COMPLETE"


def test_pytest_failure_without_parsed_nodes_is_partial() -> None:
    log = "============================= 1 failed in 0.12s ============================="
    check = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=1,
        duration_ms=1,
        stdout_tail=log,
    )
    status, issues = assess_pytest_input_integrity(check, parsed_count=0, log_text=log)
    assert status == "PARTIAL"
    assert issues


def test_merge_prefers_invalid_over_complete() -> None:
    status, issues = merge_input_integrity_status(
        ("COMPLETE", []),
        ("INVALID", ["pytest gate timed out"]),
    )
    assert status == "INVALID"
    assert issues


def test_mypy_partial_pytest_nodes_promotes_merged_complete() -> None:
    """Mixed-scope mypy PARTIAL не блокирует handoff при именованном pytest FAIL."""
    node = "tests/unit/channels/max/invest_yalta/test_funnel_self_walk.py::test_max_funnel_self_walk_green"
    log = f"FAILED {node} - AssertionError: stuck on invite\n"
    bundle = _bundle(1, log)
    mypy_log = (
        "opsx/foo.py:1: error: typo\n"
        "Found 1 error in 1 file (checked 2 source files)\n"
    )
    mypy_fail = bundle.mypy.model_copy(
        update={"exit_code": 1, "stdout_tail": mypy_log},
    )
    bundle = bundle.model_copy(update={"mypy": mypy_fail})
    diag = extract_gate_diagnostics(
        bundle,
        pytest_parse_log=log,
        mypy_parse_log=mypy_log,
    )
    assert node.split("::", 1)[-1] in str(diag.pytest)
    assert diag.input_integrity_status == "COMPLETE"


def test_reconcile_handoff_promotes_partial_when_pytest_node_present() -> None:
    node = "tests/unit/foo.py::test_bar"
    handoff = {
        "pytest": [f"pytest: {node} - AssertionError: boom"],
        "input_integrity_status": "PARTIAL",
        "input_integrity_issues": [
            "mypy_reported_error_count=3 != mypy_parsed_issue_count=0",
        ],
    }
    status, issues = reconcile_handoff_input_integrity(handoff, pytest_exit_nonzero=True)
    assert status == "COMPLETE"
    assert issues


def test_empty_pytest_fail_log_stays_partial_without_nodes() -> None:
    handoff = {
        "pytest": [],
        "input_integrity_status": "PARTIAL",
        "input_integrity_issues": [
            "pytest exit non-zero but no FAILED/ERROR node ids parsed from log",
        ],
    }
    status, _ = reconcile_handoff_input_integrity(handoff, pytest_exit_nonzero=True)
    assert status == "PARTIAL"
    status2, _ = promote_integrity_when_pytest_nodes_present(
        "PARTIAL",
        handoff["input_integrity_issues"],
        pytest_exit_nonzero=True,
        pytest_node_count=0,
    )
    assert status2 == "PARTIAL"


def test_suite_timeout_structured_evidence_is_complete_integrity() -> None:
    node = "tests/unit/test_pytest_collection_scope.py::test_broad_pytest_dot_skips_non_product_trees"
    log = f"FAILED {node} - PYTEST_SUITE_TIMEOUT\n"
    evidence = format_pytest_timeout_evidence(
        scope="tests",
        timeout_s=90.0,
        test_count=2023,
        per_test_s=5.0,
        last_node_id=node,
        stdout_tail=log,
        stderr_tail="",
    )
    assert is_pytest_structured_gate_evidence(evidence)
    check = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=1,
        duration_ms=90_000,
        stdout_tail=log,
        error=evidence,
    )
    status, issues = assess_pytest_input_integrity(check, parsed_count=1, log_text=log)
    assert status == "COMPLETE"
    assert not issues
    bundle = QualityGateRawBundle(
        pytest=check,
        pyright=check.model_copy(update={"check_id": "pyright", "exit_code": 0, "error": None}),
        mypy=check.model_copy(
            update={
                "check_id": "mypy",
                "exit_code": 0,
                "error": None,
                "stdout_tail": "Success: no issues found",
            },
        ),
    )
    diag = extract_gate_diagnostics(bundle, pytest_parse_log=log)
    assert diag.input_integrity_status == "COMPLETE"
    assert node in str(diag.pytest)


def test_kill_failed_evidence_stays_subprocess_error() -> None:
    payload = '{"kind": "PYTEST_KILL_FAILED", "scope": "tests", "pid": 1}'
    assert not is_pytest_structured_gate_evidence(payload)
    check = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=1,
        duration_ms=1,
        error=payload,
    )
    status, issues = assess_pytest_input_integrity(check, parsed_count=0, log_text="")
    assert status == "INVALID"
    assert issues and "subprocess error" in issues[0]


def test_call_stall_structured_evidence_replaces_collection_fallback() -> None:
    """Регрессия visual 105238: progress-only log + JSON stall → node id, не collection failed."""
    node = (
        "tests/smoke/test_poetry_dev_toolchain_smoke.py"
        "::test_venv_binaries_match_static_check_contract"
    )
    progress_only = "\n".join(
        f"OPSX_GATE node={node} when=call"
        for _ in range(50)
    )
    evidence = format_pytest_stall_evidence(
        scope="tests",
        stall_limit_s=300.0,
        timeout_s=490.0,
        test_count=2024,
        per_test_s=5.0,
        last_node_id=node,
        stdout_tail=progress_only,
        stderr_tail="",
        stall_when="call",
        hang_product_eligible=True,
    )
    obs = pytest_observations_from_structured_gate_error(evidence)
    assert obs and "PYTEST_STALL" in obs[0] and node in obs[0]
    check = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=1,
        duration_ms=5000,
        stdout_tail=progress_only[-3500:],
        error=evidence,
    )
    bundle = _bundle(1, progress_only)
    bundle = bundle.model_copy(update={"pytest": check})
    diag = extract_gate_diagnostics(bundle, pytest_parse_log=progress_only)
    assert diag.pytest == obs
    assert "collection failed" not in str(diag.pytest)
    assert diag.input_integrity_status == "COMPLETE"
    loaded = input_integrity_from_gate_diagnostics(diag.as_handoff_dict())
    assert loaded.status == "COMPLETE"


def test_unknown_node_stall_resolves_from_stdout_tail() -> None:
    """Регрессия visual resume: JSON stall с UNKNOWN_NODE, node id в stdout_tail."""
    node = (
        "tests/smoke/test_poetry_dev_toolchain_smoke.py"
        "::test_venv_binaries_match_static_check_contract"
    )
    progress_only = "\n".join(
        [
            f"OPSX_GATE_PROGRESS node={node} when=setup event=start",
            f"OPSX_GATE_PROGRESS node={node} when=call event=start",
        ]
    )
    evidence = format_pytest_stall_evidence(
        scope="tests",
        stall_limit_s=300.0,
        timeout_s=490.0,
        test_count=100,
        per_test_s=5.0,
        last_node_id="UNKNOWN_NODE",
        stdout_tail=progress_only,
        stderr_tail="",
        stall_when="call",
        hang_product_eligible=True,
    )
    obs = pytest_observations_from_structured_gate_error(evidence)
    assert obs and node in obs[0] and "PYTEST_STALL" in obs[0]
    check = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=1,
        duration_ms=5000,
        stdout_tail=progress_only,
        error=evidence,
    )
    status, issues = assess_pytest_input_integrity(check, parsed_count=1, log_text=progress_only)
    assert status == "COMPLETE"
    assert not issues


def test_group_error_reads_complete_from_handoff_dict() -> None:
    """Handoff diagnostics с pytest FAIL не дают INVALID при COMPLETE producer."""
    node = "tests/unit/foo.py::test_bar"
    log = f"FAILED {node} - AssertionError: boom"
    diag = extract_gate_diagnostics(_bundle(1, log), pytest_parse_log=log)
    handoff = diag.as_handoff_dict()
    assert handoff["input_integrity_status"] == "COMPLETE"
    loaded = input_integrity_from_gate_diagnostics(handoff)
    assert loaded.status == "COMPLETE"


def test_visual_111943_progress_only_stall_without_gate_error_json() -> None:
    """Регрессия agent-20261113-111943: call hang без JSON в ``GateCheckResult.error``."""
    node = (
        "tests/smoke/test_poetry_dev_toolchain_smoke.py"
        "::test_venv_binaries_match_static_check_contract"
    )
    progress_only = "\n".join(
        [
            f"OPSX_GATE_PROGRESS node={node} when=setup event=start",
            f"OPSX_GATE_PROGRESS node={node} when=call event=start",
        ]
    )
    check = GateCheckResult(
        check_id="pytest",
        command=["pytest"],
        exit_code=1,
        duration_ms=30_000,
        stdout_tail=progress_only[-3500:],
        error=None,
    )
    bundle = _bundle(1, progress_only)
    bundle = bundle.model_copy(update={"pytest": check})
    diag = extract_gate_diagnostics(bundle, pytest_parse_log=progress_only)
    assert diag.pytest
    assert "PYTEST_STALL" in diag.pytest[0]
    assert node in diag.pytest[0]
    assert "collection failed" not in diag.pytest[0].lower()
    assert diag.input_integrity_status == "COMPLETE"
