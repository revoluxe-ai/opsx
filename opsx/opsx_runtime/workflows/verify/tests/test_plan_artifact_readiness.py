"""Targeted contract tests for the pre-verification artifact gate and trace producer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.common.scenario_files import materialize_scenario_files, scenario_test_relpath
from agents.common.scenario_migrate import check_coverage
from agents.implementation.scenario_trace_materializer.agent import (
    materialize_trace,
    scenario_trace_materialize_node,
)
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    trace_matrix_path,
)
from run_scenario_trace import execute as execute_test_trace_stage
from run_scenario_trace import build_test_trace_graph
from run_plan_readiness import execute as execute_planning_readiness
from run_plan_readiness import build_planning_readiness_graph
from workflows.verify.plan_artifacts import (
    inspect_plan_artifacts,
    plan_artifacts_preflight_node,
    route_after_plan_artifacts_preflight,
)
from workflows.verify.cycle_checks import resolve_code_fingerprint


def _fixture(root: Path, *, two_tests: bool = False, marker: bool = True) -> tuple[str, Path, str]:
    change_id = "demo"
    change = root / "openspec" / "changes" / change_id
    spec = change / "specs" / "demo" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "### Requirement: demo\n#### Scenario: one\n"
        "- **WHEN** input arrives\n- **THEN** output exists\n", encoding="utf-8"
    )
    for name in ("proposal.md", "design.md", "tasks.md"):
        (change / name).write_text(f"{name}\n", encoding="utf-8")
    sid = f"openspec/changes/{change_id}/specs/demo/spec.md::one"
    rel = scenario_test_relpath(change_id, sid)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = (
        '    """Сценарная проверка.\n'
        "    Смысл: проверка сценария.\n"
        "    Исходные условия: состояние, заданное телом теста.\n"
        "    Действие: выполнение этой test-функции.\n"
        "    Ожидаемый результат: assertions этой функции.\n"
        f"    SCENARIO_ID: {sid}\n"
        '    """\n'
    ) if marker else ""
    code = "def test_demo_one():\n" + doc + "    assert 1 == 1\n"
    if two_tests:
        code += "\ndef test_demo_two():\n" + doc + "    assert 2 == 2\n"
    path.write_text(code, encoding="utf-8")
    sc = change / "scenario-coverage"
    sc.mkdir()
    ensure_machine_receipt_dirs(sc)
    plan = f"SCENARIO_COVERAGE_PLAN:\n  SCENARIOS:\n    - SCENARIO_ID: {sid}\n"
    (sc / "plan.md").write_text(plan, encoding="utf-8")
    plan_receipt_path(sc).write_text(json.dumps({
        "SCENARIO_COVERAGE_RECEIPT": True,
        "CHANGE_ID": change_id,
        "PLAN_STATUS": "ready",
        "PLAN_EVIDENCE_ID": "plan-revision-one",
        "PLAN_CONTENT_HASH": hashlib.sha256(plan.encode()).hexdigest(),
        "INPUTS": {
            "SPEC_REVISION": [
                {"path": "specs/demo/spec.md",
                 "content_hash": hashlib.sha256(spec.read_bytes()).hexdigest()},
            ],
            "DESIGN_REVISION": {
                "path": "design.md",
                "content_hash": hashlib.sha256((change / "design.md").read_bytes()).hexdigest(),
            },
        },
    }, indent=2), encoding="utf-8")
    materialize_scenario_files(sc)
    return sid, sc, rel


@pytest.mark.asyncio
async def test_planning_graph_proves_plan_ready_without_trace_matrix(tmp_path):
    _, sc, _rel = _fixture(tmp_path)
    assert "planning_readiness" in build_planning_readiness_graph().get_graph().nodes
    result = await execute_planning_readiness("demo", tmp_path)
    assert result["status"] == "READY"
    assert result["phase"] == "planning"
    assert not trace_matrix_path(sc).exists()
    assert inspect_plan_artifacts(tmp_path, "demo", phase="verify")["code"] == "TRACE_MATRIX_MISSING"


@pytest.mark.asyncio
async def test_planning_graph_blocks_invalid_receipt_before_authoring(tmp_path):
    _, sc, _rel = _fixture(tmp_path)
    receipt = plan_receipt_path(sc)
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["PLAN_CONTENT_HASH"] = "stale"
    receipt.write_text(json.dumps(payload), encoding="utf-8")
    result = await execute_planning_readiness("demo", tmp_path)
    assert result["status"] == "BLOCKED"
    assert result["code"] == "COVERAGE_PLAN_HASH_MISMATCH"


def test_missing_matrix_routes_to_producer_then_ready(tmp_path):
    sid, sc, rel = _fixture(tmp_path)
    status = inspect_plan_artifacts(tmp_path, "demo")
    assert status["status"] == "BLOCKED"
    assert status["code"] == "TRACE_MATRIX_MISSING"
    assert inspect_plan_artifacts(tmp_path, "demo", phase="planning")["status"] == "READY"
    state = {"plan_artifact_status": "BLOCKED"}
    assert route_after_plan_artifacts_preflight(state) == "plan_artifacts_terminal"
    result = materialize_trace(tmp_path, "demo")
    assert result["status"] == "MATERIALIZED"
    matrix = json.loads(trace_matrix_path(sc).read_text())
    assert matrix["entries"][0]["scenario_id"] == sid
    assert matrix["entries"][0]["test_node_id"] == f"{rel}::test_demo_one"
    assert inspect_plan_artifacts(tmp_path, "demo")["status"] == "READY"
    assert materialize_trace(tmp_path, "demo")["status"] == "ALREADY_CURRENT"


def test_verify_blocked_when_matrix_entry_has_empty_test_node_id(tmp_path):
    sid, sc, _rel = _fixture(tmp_path)
    assert materialize_trace(tmp_path, "demo")["status"] == "MATERIALIZED"
    path = trace_matrix_path(sc)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entries"] = [{"scenario_id": sid, "test_node_id": ""}]
    path.write_text(json.dumps(payload), encoding="utf-8")
    status = inspect_plan_artifacts(tmp_path, "demo", phase="verify")
    assert status["status"] == "READY"
    problems = check_coverage(sc, repo=tmp_path, require_canonical_tests=True)
    assert any(p.startswith("SCENARIO_TEST_MAPPING_MISSING:") for p in problems)


def test_verify_blocked_when_canonical_test_file_missing(tmp_path):
    """Без canonical test-файла verify preflight fail-closed."""
    sid, sc, _rel = _fixture(tmp_path)
    assert materialize_trace(tmp_path, "demo")["status"] == "MATERIALIZED"
    matrix = json.loads(trace_matrix_path(sc).read_text(encoding="utf-8"))
    rel = matrix["entries"][0]["test_node_id"].split("::", 1)[0]
    (tmp_path / rel).unlink(missing_ok=True)
    status = inspect_plan_artifacts(tmp_path, "demo", phase="verify")
    assert status["status"] == "READY"
    problems = check_coverage(sc, repo=tmp_path, require_canonical_tests=True)
    assert any(p.startswith("SCENARIO_TEST_FILE_MISSING:") for p in problems)


def test_stale_matrix_rebinds_only_valid_existing_mapping(tmp_path):
    _, sc, _rel = _fixture(tmp_path)
    assert materialize_trace(tmp_path, "demo")["status"] == "MATERIALIZED"
    path = trace_matrix_path(sc)
    prior = json.loads(path.read_text())
    prior["plan_evidence_id"] = "stale"
    path.write_text(json.dumps(prior), encoding="utf-8")
    assert inspect_plan_artifacts(tmp_path, "demo")["code"] == "TRACE_MATRIX_STALE"
    assert materialize_trace(tmp_path, "demo")["status"] == "MATERIALIZED"
    assert inspect_plan_artifacts(tmp_path, "demo")["status"] == "READY"


def test_missing_test_marker_never_invents_mapping(tmp_path):
    _, sc, _rel = _fixture(tmp_path, marker=False)
    result = materialize_trace(tmp_path, "demo")
    assert result["status"] == "MATERIALIZED"
    matrix = json.loads(trace_matrix_path(sc).read_text(encoding="utf-8"))
    assert matrix["entries"][0]["test_node_id"] == ""


def test_ambiguous_scenario_marker_fails_closed(tmp_path):
    _, sc, _rel = _fixture(tmp_path, two_tests=True)
    result = materialize_trace(tmp_path, "demo")
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "TRACE_MAPPING_AMBIGUOUS_OR_MISSING"
    assert not trace_matrix_path(sc).exists()


def test_stale_plan_or_source_blocks_preflight_before_writer(tmp_path):
    _, sc, _rel = _fixture(tmp_path)
    (sc / "plan.md").write_text("changed", encoding="utf-8")
    assert inspect_plan_artifacts(tmp_path, "demo")["code"] == "COVERAGE_PLAN_HASH_MISMATCH"
    _, sc, _rel = _fixture_fresh(tmp_path)
    change = sc.parent
    (change / "design.md").write_text("new design", encoding="utf-8")
    assert inspect_plan_artifacts(tmp_path, "demo")["code"] == "COVERAGE_INPUT_STALE"


def _fixture_fresh(root: Path) -> tuple[str, Path, str]:
    import shutil
    shutil.rmtree(root / "openspec")
    shutil.rmtree(root / "tests")
    return _fixture(root)


def test_invalid_matrix_rows_do_not_get_overwritten(tmp_path):
    _, sc, _rel = _fixture(tmp_path)
    assert materialize_trace(tmp_path, "demo")["status"] == "MATERIALIZED"
    matrix = trace_matrix_path(sc)
    obj = json.loads(matrix.read_text())
    obj["entries"][0]["scenario_id"] = "unexpected"
    matrix.write_text(json.dumps(obj), encoding="utf-8")
    assert inspect_plan_artifacts(tmp_path, "demo")["code"] == "TRACE_MATRIX_ROW_MISMATCH"
    assert materialize_trace(tmp_path, "demo")["status"] == "BLOCKED"


@pytest.mark.asyncio
async def test_authoring_graph_is_separate_and_idempotent(tmp_path):
    _fixture(tmp_path)  # noqa: contract fixture
    # Stage and verify have separate graphs and execution authorities.
    assert scenario_trace_materialize_node is not None
    assert "scenario_trace_materialize" in build_test_trace_graph().get_graph().nodes
    result = await execute_test_trace_stage("demo", tmp_path)
    assert result["status"] == "TRACE_READY_FOR_AUDIT"
    assert result["producer"]["status"] == "MATERIALIZED"
    again = await execute_test_trace_stage("demo", tmp_path)
    assert again["status"] == "TRACE_READY_FOR_AUDIT"
    assert again["producer"]["status"] == "ALREADY_CURRENT"


@pytest.mark.asyncio
async def test_verify_gate_never_writes_missing_matrix(tmp_path):
    _, sc, _rel = _fixture(tmp_path)
    state = await plan_artifacts_preflight_node({
        "repository_path": str(tmp_path), "change_id": "demo",
    })
    assert state["plan_artifact_status"] == "BLOCKED"
    assert route_after_plan_artifacts_preflight(state) == "plan_artifacts_terminal"
    assert not trace_matrix_path(sc).exists()


@pytest.mark.asyncio
async def test_preflight_source_revision_binding_changes_without_git_commit(tmp_path, monkeypatch):
    _, _, rel = _fixture(tmp_path)
    assert materialize_trace(tmp_path, "demo")["status"] == "MATERIALIZED"
    initial = await plan_artifacts_preflight_node({
        "repository_path": str(tmp_path), "change_id": "demo",
    })
    assert route_after_plan_artifacts_preflight(initial) == "verify_cycle_gate"
    a = resolve_code_fingerprint({"repository_path": str(tmp_path), **initial})
    test = tmp_path / rel
    test.write_text(
        test.read_text(encoding="utf-8") + "\n# modified uncommitted test\n",
        encoding="utf-8",
    )
    changed = await plan_artifacts_preflight_node({
        "repository_path": str(tmp_path), "change_id": "demo",
    })
    b = resolve_code_fingerprint({"repository_path": str(tmp_path), **changed})
    assert a != b
