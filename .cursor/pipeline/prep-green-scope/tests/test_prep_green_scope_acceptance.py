"""Acceptance: prep-green mechanism (handoff, convergence, gates, apply)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
PREP_PKG = Path(__file__).resolve().parents[1]
CONTRACTS = REPO_ROOT / ".cursor" / "pipeline" / "contracts"
_HANDOFF = REPO_ROOT / ".cursor" / "static-check" / "contracts"
for p in (PREP_PKG, CONTRACTS, _HANDOFF):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import prep_green_full_handoff as pgfh  # noqa: E402
from acceptance_fixtures import (  # noqa: E402
    build_handoff,
    build_handoff_from_worktree,
    derive_packets_from_worktree,
    init_git_baseline,
    iteration0_packets,
    pytest_unit_packet_with_nodes,
    touch_product_file,
    write_ambiguous_param_test,
    write_complex_marks_param_test,
    write_mini_repo,
    worktree_input_fingerprint,
)
from apply_disable import (  # noqa: E402
    apply_disable_delta,
    apply_mypy_modules_monotonic,
    prepare_delta,
    snapshot_mutation_state,
)
from artifacts import artifact_is_stale, write_probe_and_plan  # noqa: E402
from convergence_runner import run_convergence_runner  # noqa: E402
from disable_plan import DestructivePolicy, DisablePlan, ProbeGate, extract_disable_plan  # noqa: E402
from full_check import FullStaticCheckResult  # noqa: E402
from handoff_consume import HandoffConsumeError, load_full_from_handoff  # noqa: E402
from pytest_disable_validate import verify_pytest_plan_applied, validate_pytest_disable  # noqa: E402
from required_test_ids_gate import audit_required_test_ids  # noqa: E402
from scenario_coverage_audit import audit_gate_integrity  # noqa: E402
from change_implementation_paths import derive_change_required_production_paths  # noqa: E402
from scenario_gate import audit_required_test_ids as gate_reexport  # noqa: E402


def _caps(root: Path) -> dict:
    return yaml.safe_load((root / "opsx/project/capabilities.yaml").read_text(encoding="utf-8"))


def _full(root: Path, handoff: dict) -> FullStaticCheckResult:
    return load_full_from_handoff(handoff)


def test_static_check_command_mandates_prep_green_handoff_block() -> None:
    text = (REPO_ROOT / ".cursor/commands/opsx-static-check.md").read_text(encoding="utf-8")
    assert "PREP_GREEN_FULL_HANDOFF" in text
    assert "fan_in_build_prep_green_handoff" in text or "prep_green_full_handoff" in text


def test_worker_packet_fan_in_to_disable_plan(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    body = "\n".join(
        [
            "CHECK_ID: pytest-unit",
            "STATUS: FAIL",
            "executed_count: 1",
            "- filepath: n/a",
            "  symbol_or_node: tests/test_unit_fail.py::test_fail",
            "  severity: test-failure",
        ]
    )
    packet = pgfh.parse_worker_packet_text(body)
    packets = iteration0_packets()
    packets[3] = packet
    handoff = pgfh.fan_in_build_prep_green_handoff(
        repo_root=REPO_ROOT,
        overall_status="FAIL",
        mode="FULL",
        worktree_fingerprint=worktree_input_fingerprint(tmp_path),
        check_packets=packets,
        destructive_policy="not_applicable",
    )
    full = _full(tmp_path, handoff)
    plan = extract_disable_plan(full, capabilities=_caps(tmp_path), repo_root=tmp_path)
    assert "tests/test_unit_fail.py::test_fail" in plan.pytest_nodes


def test_handoff_producer_consumer_matches_schema(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    handoff = build_handoff(
        tmp_path,
        status="FAIL",
        fingerprint=worktree_input_fingerprint(tmp_path),
        packets=iteration0_packets(),
    )
    assert handoff["schema"] == pgfh.HANDOFF_SCHEMA
    assert handoff["static_check_config_identity"]
    assert handoff["input_snapshot_identity"]
    assert handoff["probe_execution_identity"]
    full = _full(tmp_path, handoff)
    plan = extract_disable_plan(full, capabilities=_caps(tmp_path), repo_root=tmp_path)
    assert plan.probe_gate is ProbeGate.AWAITING_GO
    assert "tests/test_integration_fail.py::test_integration_fail" in plan.pytest_nodes


def test_sequential_convergence_apply_then_full_handoff(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    initial = build_handoff_from_worktree(tmp_path)
    full_runs: list[str] = []

    def run_full_after_apply() -> dict:
        handoff = build_handoff_from_worktree(tmp_path)
        full_runs.append(handoff["fingerprint"])
        return handoff

    verdict = run_convergence_runner(
        tmp_path,
        initial_handoff=initial,
        run_full_after_apply=run_full_after_apply,
        capabilities=_caps(tmp_path),
    )
    assert verdict.status.value == "PASS"
    assert verdict.verdict == "PREP_GREEN_SCOPE_READY"
    assert verdict.convergence_rerun_consumed is True
    assert len(full_runs) >= 1
    assert full_runs[0] != initial["fingerprint"]


def test_baseline_normalizer_pyright_blocked_awaiting_go(tmp_path: Path) -> None:
    test_baseline_normalizer_pyright_blocked_scope_fallback(tmp_path)


def test_baseline_normalizer_pyright_blocked_scope_fallback(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    packets = iteration0_packets()
    packets[0] = {"CHECK_ID": "pyright-strict", "STATUS": "BLOCKED", "FINDINGS": []}
    handoff = build_handoff(
        tmp_path,
        status="FAIL",
        fingerprint=worktree_input_fingerprint(tmp_path),
        packets=packets,
    )
    plan = extract_disable_plan(_full(tmp_path, handoff), capabilities=_caps(tmp_path), repo_root=tmp_path)
    assert plan.probe_gate is ProbeGate.AWAITING_GO
    assert plan.pyright_files
    assert "src/bad_pyright.py" in plan.pyright_files


def test_baseline_normalizer_mypy_not_executed_scope_fallback(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    packets = iteration0_packets()
    packets[1] = {"CHECK_ID": "mypy", "STATUS": "NOT_EXECUTED", "FINDINGS": []}
    handoff = build_handoff(
        tmp_path,
        status="FAIL",
        fingerprint=worktree_input_fingerprint(tmp_path),
        packets=packets,
    )
    plan = extract_disable_plan(_full(tmp_path, handoff), capabilities=_caps(tmp_path), repo_root=tmp_path)
    assert plan.probe_gate is ProbeGate.AWAITING_GO
    assert plan.mypy_modules
    assert "bad_mypy" in plan.mypy_modules


def test_baseline_normalizer_undeclared_destructive_not_applicable(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy=None)
    handoff = build_handoff(
        tmp_path,
        status="FAIL",
        fingerprint=worktree_input_fingerprint(tmp_path),
        packets=iteration0_packets(),
    )
    plan = extract_disable_plan(_full(tmp_path, handoff), capabilities=_caps(tmp_path), repo_root=tmp_path)
    assert plan.probe_gate is ProbeGate.AWAITING_GO
    assert plan.destructive_policy is DestructivePolicy.UNRESOLVED


def test_integration_suite_not_executed_yields_wave_selector_disable_plan(
    tmp_path: Path,
) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    packets = [
        {"CHECK_ID": "pyright-strict", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "mypy", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "pytest-targeted", "STATUS": "SKIPPED", "NOTES": "not_applicable"},
        {"CHECK_ID": "pytest-unit", "STATUS": "PASS", "executed_count": 1, "FINDINGS": []},
        {
            "CHECK_ID": "pytest-integration",
            "STATUS": "BLOCKED",
            "NOTES": "suite_not_executed",
            "executed_count": 0,
            "FINDINGS": [],
        },
        {
            "CHECK_ID": "pytest-destructive",
            "STATUS": "BLOCKED",
            "NOTES": "suite_not_executed",
            "executed_count": 0,
        },
    ]
    handoff = build_handoff(
        tmp_path,
        status="FAIL",
        fingerprint=worktree_input_fingerprint(tmp_path),
        packets=packets,
    )
    plan = extract_disable_plan(_full(tmp_path, handoff), capabilities=_caps(tmp_path), repo_root=tmp_path)
    assert plan.probe_gate is ProbeGate.AWAITING_GO
    assert "tests/test_integration_fail.py::test_integration_fail" in plan.pytest_nodes


def test_integration_suite_not_executed_apply_enables_next_full_pass(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    packets = [
        {"CHECK_ID": "pyright-strict", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "mypy", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "pytest-targeted", "STATUS": "SKIPPED", "NOTES": "not_applicable"},
        {"CHECK_ID": "pytest-unit", "STATUS": "PASS", "executed_count": 1, "FINDINGS": []},
        {
            "CHECK_ID": "pytest-integration",
            "STATUS": "BLOCKED",
            "NOTES": "suite_not_executed",
            "executed_count": 0,
            "FINDINGS": [],
        },
    ]
    handoff = build_handoff(
        tmp_path,
        status="FAIL",
        fingerprint=worktree_input_fingerprint(tmp_path),
        packets=packets,
    )
    plan = extract_disable_plan(_full(tmp_path, handoff), capabilities=_caps(tmp_path), repo_root=tmp_path)
    node = "tests/test_integration_fail.py::test_integration_fail"
    assert node in plan.pytest_nodes
    _ = apply_disable_delta(tmp_path, DisablePlan(pytest_nodes=[node]))
    rerun_packets = derive_packets_from_worktree(tmp_path)
    integ = next(p for p in rerun_packets if p["CHECK_ID"] == "pytest-integration")
    assert integ["STATUS"] == "PASS"


def test_executed_integration_fail_without_nodes_blocks_incomplete_handoff(
    tmp_path: Path,
) -> None:
    """247 failed / 189 passed with empty handoff must not whole-wave skip ~426 nodes."""

    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    packets = [
        {"CHECK_ID": "pyright-strict", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "mypy", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "pytest-targeted", "STATUS": "SKIPPED", "NOTES": "not_applicable"},
        {"CHECK_ID": "pytest-unit", "STATUS": "PASS", "executed_count": 1, "FINDINGS": []},
        {
            "CHECK_ID": "pytest-integration",
            "STATUS": "FAIL",
            "executed_count": 436,
            "NOTES": "247_failed_189_passed",
            "FINDINGS": [],
        },
        {
            "CHECK_ID": "pytest-destructive",
            "STATUS": "BLOCKED",
            "NOTES": "suite_not_executed",
            "executed_count": 0,
            "FINDINGS": [],
        },
    ]
    handoff = build_handoff(
        tmp_path,
        status="FAIL",
        fingerprint=worktree_input_fingerprint(tmp_path),
        packets=packets,
    )
    assert handoff["handoff_complete"] is False
    plan = extract_disable_plan(_full(tmp_path, handoff), capabilities=_caps(tmp_path), repo_root=tmp_path)
    assert plan.probe_gate is ProbeGate.BLOCKED_INCOMPLETE_HANDOFF
    assert "pytest-integration" in plan.gate_reasons[0]
    assert "tests/test_integration_fail.py::test_integration_fail" not in plan.pytest_nodes


def test_executed_integration_fail_uses_exact_failed_nodes_only(tmp_path: Path) -> None:
    """Acceptance: N failed node ids in handoff → N skips; passing nodes stay enabled."""

    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    failed_nodes = [f"tests/bulk_int.py::test_failed_{i}" for i in range(247)]
    passing_node = "tests/bulk_int.py::test_still_passes"
    findings = [
        {
            "filepath": "n/a",
            "symbol_or_node": node,
            "severity": "test-failure",
        }
        for node in failed_nodes
    ]
    packets = [
        {"CHECK_ID": "pyright-strict", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "mypy", "STATUS": "PASS", "FINDINGS": []},
        {"CHECK_ID": "pytest-targeted", "STATUS": "SKIPPED", "NOTES": "not_applicable"},
        {"CHECK_ID": "pytest-unit", "STATUS": "PASS", "executed_count": 1, "FINDINGS": []},
        {
            "CHECK_ID": "pytest-integration",
            "STATUS": "FAIL",
            "executed_count": 436,
            "NOTES": "247_failed_189_passed",
            "FINDINGS": findings,
        },
        {
            "CHECK_ID": "pytest-destructive",
            "STATUS": "BLOCKED",
            "NOTES": "suite_not_executed",
            "executed_count": 0,
            "FINDINGS": [],
        },
    ]
    handoff = build_handoff(
        tmp_path,
        status="FAIL",
        fingerprint=worktree_input_fingerprint(tmp_path),
        packets=packets,
    )
    assert handoff["handoff_complete"] is True
    plan = extract_disable_plan(_full(tmp_path, handoff), capabilities=_caps(tmp_path), repo_root=tmp_path)
    assert plan.probe_gate is ProbeGate.AWAITING_GO
    assert len(plan.pytest_nodes) == 247
    assert passing_node not in plan.pytest_nodes
    assert "tests/test_integration_fail.py::test_integration_fail" not in plan.pytest_nodes


def test_pytest_wave_collect_uses_marker_inventory_when_collect_fails(
    tmp_path: Path, monkeypatch
) -> None:
    from pytest_wave_collect import collect_pytest_nodes_for_wave

    write_mini_repo(tmp_path, destructive_policy="not_applicable")

    def _fail(*_args, **_kwargs):
        class _P:
            returncode = 2
            stdout = ""
            stderr = "collect failed"

        return _P()

    monkeypatch.setattr("pytest_wave_collect.subprocess.run", _fail)
    wave = collect_pytest_nodes_for_wave(tmp_path, "pytest-integration")
    assert wave.collect_failed is True
    assert wave.used_fallback is True
    assert "tests/test_integration_fail.py::test_integration_fail" in wave.nodes


def test_scenario_audit_gate_integrity_pyright_change_required(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    init_git_baseline(tmp_path)
    touch_product_file(tmp_path, "src/bad_pyright.py")
    paths = derive_change_required_production_paths(tmp_path, "demo")
    assert "src/bad_pyright.py" in paths
    _ = apply_disable_delta(tmp_path, DisablePlan(pyright_files=["src/bad_pyright.py"]))
    audit = audit_gate_integrity(tmp_path, "demo")
    assert audit.gate_integrity == "FAIL"
    assert audit.gap_class == "REQUIRED_STATIC_CHECK_DISABLED"
    assert any(r.change_path == "src/bad_pyright.py" for r in audit.required_static_check_rows)


def test_scenario_audit_gate_integrity_mypy_change_required(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    init_git_baseline(tmp_path)
    touch_product_file(tmp_path, "src/bad_mypy.py")
    assert "src/bad_mypy.py" in derive_change_required_production_paths(tmp_path, "demo")
    _ = apply_disable_delta(tmp_path, DisablePlan(mypy_modules=["bad_mypy"]))
    audit = audit_gate_integrity(tmp_path, "demo")
    assert audit.gate_integrity == "FAIL"
    assert audit.gap_class == "REQUIRED_STATIC_CHECK_DISABLED"
    assert any(r.mechanism == "mypy_ignore_errors" for r in audit.required_static_check_rows)


def test_scenario_audit_gate_integrity_unrelated_legacy_ignore_ok(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    init_git_baseline(tmp_path)
    touch_product_file(tmp_path, "src/bad_pyright.py")
    _ = apply_disable_delta(tmp_path, DisablePlan(pyright_files=["src/legacy_ignored.py"]))
    audit = audit_gate_integrity(tmp_path, "demo")
    assert audit.gate_integrity == "PASS"
    assert audit.required_static_check_rows == []


def test_pyright_blocked_forbids_ready(tmp_path: Path) -> None:
    """Legacy name: BLOCKED pyright no longer blocks STAGE1 (baseline normalizer)."""

    test_baseline_normalizer_pyright_blocked_awaiting_go(tmp_path)


def test_mypy_not_executed_forbids_ready(tmp_path: Path) -> None:
    """Legacy name: NOT_EXECUTED mypy no longer blocks STAGE1."""

    test_baseline_normalizer_mypy_not_executed_scope_fallback(tmp_path)


def test_parametrize_only_red_param_skipped(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    plan = DisablePlan(pytest_nodes=["tests/test_param.py::test_param[2]"])
    result = apply_disable_delta(tmp_path, plan)
    assert result.applied.pytest_nodes == ["tests/test_param.py::test_param[2]"]
    assert result.recovery_contract == "none"
    text = (tmp_path / "tests/test_param.py").read_text(encoding="utf-8")
    assert "pytest.param(2" in text
    assert "pytest.mark.skip" in text


def test_ambiguous_parametrize_fails_closed_zero_writes(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    write_ambiguous_param_test(tmp_path)
    before = snapshot_mutation_state(tmp_path)
    plan = DisablePlan(
        pyright_files=["src/bad_pyright.py"],
        pytest_nodes=["tests/test_ambiguous.py::test_tuple_param[case-a]"],
    )
    prep = prepare_delta(tmp_path, plan)
    assert prep.ok is False
    result = apply_disable_delta(tmp_path, plan)
    assert result.recovery_contract == "no_writes_attempted"
    assert result.applied.pytest_nodes == []
    assert snapshot_mutation_state(tmp_path) == before


def test_partial_apply_preflight_no_pyright_when_pytest_blocked(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    write_ambiguous_param_test(tmp_path)
    before = snapshot_mutation_state(tmp_path)
    plan = DisablePlan(
        pyright_files=["src/bad_pyright.py"],
        mypy_modules=["bad_mypy"],
        pytest_nodes=["tests/test_ambiguous.py::test_tuple_param[case-a]"],
    )
    result = apply_disable_delta(tmp_path, plan)
    assert result.applied.pyright_files == []
    assert result.applied.mypy_modules == []
    assert snapshot_mutation_state(tmp_path) == before


def test_mypy_monotonic_union_preserves_legacy(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    _ = apply_mypy_modules_monotonic(tmp_path, ["mod.a", "mod.b"])
    _ = apply_mypy_modules_monotonic(tmp_path, ["mod.c"])
    text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert "legacy.unrelated" in text
    assert 'module = "mod.a"' in text
    assert 'module = "mod.b"' in text
    assert 'module = "mod.c"' in text


def test_artifact_stale_on_config_and_input_mismatch(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    full = _full(
        tmp_path,
        build_handoff(
            tmp_path,
            status="FAIL",
            fingerprint=worktree_input_fingerprint(tmp_path),
            packets=iteration0_packets(),
        ),
    )
    plan = extract_disable_plan(full, capabilities=_caps(tmp_path), repo_root=tmp_path)
    _, _ = write_probe_and_plan(tmp_path, full=full, plan=plan, capabilities=_caps(tmp_path))
    data = json.loads((tmp_path / "opsx/prep-green-scope/probe-result.json").read_text())
    stale_cfg, r1 = artifact_is_stale(
        data,
        expected_static_check_config_identity="wrong-config",
    )
    assert stale_cfg and any("static_check_config_identity" in r for r in r1)
    stale_in, r2 = artifact_is_stale(
        data,
        expected_input_snapshot_identity="wrong-input",
    )
    assert stale_in and any("input_snapshot_identity" in r for r in r2)


def test_scenario_audit_mapped_ids_disabled_proof(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    plan = DisablePlan(pytest_nodes=["tests/test_unit_fail.py::test_fail"])
    _ = apply_disable_delta(tmp_path, plan)
    mapped_a = ["tests/test_unit_fail.py::test_fail"]
    a = audit_required_test_ids(tmp_path, "demo-a", mapped_a)
    b = gate_reexport(tmp_path, "demo-a", mapped_a)
    assert a.row_verdict == b.row_verdict == "FAIL"
    assert a.gap_class == "DISABLED_PROOF_SKIP"


def test_scenario_isolation_unrelated_row_passes(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    plan = DisablePlan(pytest_nodes=["tests/test_unit_fail.py::test_fail"])
    _ = apply_disable_delta(tmp_path, plan)
    mapped_b = ["tests/test_integration_fail.py::test_integration_fail"]
    audit_b = audit_required_test_ids(tmp_path, "demo-b", mapped_b)
    assert audit_b.row_verdict == "PASS"
    assert audit_b.gap_class is None


def test_A_75_nodes_handoff_reaches_disable_plan(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    nodes = [f"tests/bulk.py::test_n{i}" for i in range(75)]
    packets = iteration0_packets()
    packets[3] = pytest_unit_packet_with_nodes(nodes)
    packets[4] = {
        "CHECK_ID": "pytest-integration",
        "STATUS": "PASS",
        "executed_count": 1,
        "FINDINGS": [],
    }
    handoff = build_handoff(
        tmp_path,
        status="FAIL",
        fingerprint=worktree_input_fingerprint(tmp_path),
        packets=packets,
    )
    assert handoff["handoff_complete"] is True
    plan = extract_disable_plan(_full(tmp_path, handoff), capabilities=_caps(tmp_path), repo_root=tmp_path)
    assert plan.probe_gate is ProbeGate.AWAITING_GO
    assert len(plan.pytest_nodes) == 75


def test_A_truncated_handoff_blocks_incomplete_plan(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    nodes = [f"tests/bulk.py::test_n{i}" for i in range(75)]
    truncated = pytest_unit_packet_with_nodes(nodes[:50])
    truncated["FINDINGS_TRUNCATED"] = "yes"
    truncated["FINDINGS_TOTAL"] = 75
    packets = iteration0_packets()
    packets[3] = truncated
    packets[4] = {
        "CHECK_ID": "pytest-integration",
        "STATUS": "PASS",
        "executed_count": 1,
        "FINDINGS": [],
    }
    handoff = build_handoff(
        tmp_path,
        status="FAIL",
        fingerprint=worktree_input_fingerprint(tmp_path),
        packets=packets,
    )
    assert handoff["handoff_complete"] is False
    plan = extract_disable_plan(_full(tmp_path, handoff), capabilities=_caps(tmp_path), repo_root=tmp_path)
    assert plan.probe_gate is ProbeGate.BLOCKED_INCOMPLETE_HANDOFF
    assert plan.pytest_nodes == []


def test_B_missing_handoff_blocked_no_fallback() -> None:
    try:
        _ = load_full_from_handoff(None)
        raise AssertionError("expected HandoffConsumeError")
    except HandoffConsumeError as exc:
        assert exc.verdict == "BLOCKED_MISSING_FULL_HANDOFF"


def test_C_function_skip_exact_and_verified(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    node = "tests/test_unit_fail.py::test_fail"
    plan = DisablePlan(pytest_nodes=[node])
    prep = prepare_delta(tmp_path, plan)
    assert prep.ok
    result = apply_disable_delta(tmp_path, plan)
    assert result.applied.pytest_nodes == [node]
    validated = validate_pytest_disable(tmp_path, node)
    assert validated is not None
    assert verify_pytest_plan_applied(validated, reason="prep-green-scope: baseline red")


def test_F_complex_marks_param_zero_writes(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    write_complex_marks_param_test(tmp_path)
    before = snapshot_mutation_state(tmp_path)
    plan = DisablePlan(pytest_nodes=["tests/test_complex_marks.py::test_complex[case-a]"])
    assert prepare_delta(tmp_path, plan).ok is False
    result = apply_disable_delta(tmp_path, plan)
    assert result.recovery_contract == "no_writes_attempted"
    assert snapshot_mutation_state(tmp_path) == before


def test_G_successful_delta_pytest_files_collectable(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    result = apply_disable_delta(
        tmp_path, DisablePlan(pytest_nodes=["tests/test_unit_fail.py::test_fail"])
    )
    assert result.recovery_contract == "none"
    paths = {p for p in result.files_changed if p.startswith("tests/")}
    assert "tests/test_unit_fail.py" in paths


def test_H_stale_execution_id_rejected(tmp_path: Path) -> None:
    write_mini_repo(tmp_path, destructive_policy="not_applicable")
    full = _full(
        tmp_path,
        build_handoff(
            tmp_path,
            status="FAIL",
            fingerprint=worktree_input_fingerprint(tmp_path),
            packets=iteration0_packets(),
        ),
    )
    plan = extract_disable_plan(full, capabilities=_caps(tmp_path), repo_root=tmp_path)
    _, _ = write_probe_and_plan(tmp_path, full=full, plan=plan, capabilities=_caps(tmp_path))
    data = json.loads((tmp_path / "opsx/prep-green-scope/probe-result.json").read_text())
    stale, reasons = artifact_is_stale(data, expected_execution_id="other-exec-id")
    assert stale is True
    assert any("execution_id" in r for r in reasons)
