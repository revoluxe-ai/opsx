"""Один сценарий — один файл плана и один test-файл."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agents.common.scenario_files import (
    materialize_scenario_files,
    plan_has_embedded_rows,
    read_scenario_ids,
    scenario_digest,
    scenario_test_relpath,
)
from agents.common.scenario_receipt_test_paths import (
    ensure_machine_receipt_dirs,
    plan_receipt_path,
    row_journal_path,
    trace_matrix_path,
)
from agents.common.scenario_migrate import docstring_satisfies, write_tests_for_change
from agents.common.planning_artifacts import inspect_plan_artifacts
from agents.verify.scenario_coverage_verify.delta_rows import impact_scope
from agents.verify.scenario_coverage_verify.evidence_contract import source_paths
from agents.common.scenario_migrate import check_coverage, write_tests_for_change
from agents.verify.scenario_coverage_verify.row_cycle import (
    RowWalkJournal,
    _bind_matrix,
    _combine,
    run_scenario_row_walk,
)
from workflows.checkpoint_resume import reset_row_walk_journal_repair_attempts
from agents.verify.scenario_coverage_verify.row_cycle_green_commit import (
    collect_green_row_commit_paths,
)


@pytest.fixture(autouse=True)
def _row_walk_disposition_test_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Walk persist/skip тесты: без полного verifier tree и semantic proof gate."""
    from agents.verify.scenario_coverage_verify import row_cycle, row_receipts
    from agents.verify.scenario_coverage_verify import evidence_contract as ev

    monkeypatch.setattr(row_receipts, "review_proof_error", lambda *_a, **_k: None)
    monkeypatch.setattr(ev, "scenario_verifier_contract_digest", lambda *_a, **_k: "0" * 64)
    if hasattr(row_cycle, "scenario_verifier_contract_digest"):
        monkeypatch.setattr(
            row_cycle, "scenario_verifier_contract_digest", lambda *_a, **_k: "0" * 64,
        )


def _coverage(root: Path, text: str) -> Path:
    change = root / "openspec" / "changes" / "demo"
    change.mkdir(parents=True, exist_ok=True)
    for name in ("proposal.md", "design.md", "tasks.md"):
        (change / name).write_text(name + "\n", encoding="utf-8")
    spec = change / "specs" / "demo" / "spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "#### Scenario: one\n- **WHEN** a\n- **THEN** b\n",
        encoding="utf-8",
    )
    coverage = change / "scenario-coverage"
    coverage.mkdir()
    ensure_machine_receipt_dirs(coverage)
    (coverage / "plan.md").write_text(text, encoding="utf-8")
    plan_receipt_path(coverage).write_text(json.dumps({
        "SCENARIO_COVERAGE_RECEIPT": True,
        "CHANGE_ID": "demo",
        "PLAN_STATUS": "ready",
        "PLAN_EVIDENCE_ID": "plan-1",
        "INPUTS": {
            "SPEC_REVISION": [{
                "path": "specs/demo/spec.md",
                "content_hash": hashlib.sha256(spec.read_bytes()).hexdigest(),
            }],
            "DESIGN_REVISION": {
                "path": "design.md",
                "content_hash": hashlib.sha256((change / "design.md").read_bytes()).hexdigest(),
            },
        },
    }), encoding="utf-8")
    return coverage


def test_plan_split_keeps_row_digest(tmp_path: Path) -> None:
    sid_a = "openspec/changes/demo/specs/demo/spec.md::one"
    sid_b = "openspec/changes/demo/specs/demo/spec.md::two"
    spec = tmp_path / "openspec/changes/demo/specs/demo/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "#### Scenario: one\n- **WHEN** a\n- **THEN** b\n\n"
        "#### Scenario: two\n- **WHEN** c\n- **THEN** d\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("def test_a():\n    assert 1\n", encoding="utf-8")
    (tests / "test_b.py").write_text("def test_b():\n    assert 2\n", encoding="utf-8")
    coverage = tmp_path / "openspec/changes/demo/scenario-coverage"
    coverage.mkdir(parents=True)
    ensure_machine_receipt_dirs(coverage)
    (coverage / "plan.md").write_text(
        f"- SCENARIO_ID: {sid_a}\n- SCENARIO_ID: {sid_b}\n",
        encoding="utf-8",
    )
    plan_receipt_path(coverage).write_text("{}", encoding="utf-8")
    trace_matrix_path(coverage).write_text(json.dumps({
        "entries": [
            {"scenario_id": sid_a, "test_node_id": "tests/test_a.py::test_a"},
            {"scenario_id": sid_b, "test_node_id": "tests/test_b.py::test_b"},
        ],
    }), encoding="utf-8")
    mapping = {
        sid_a: "tests/test_a.py::test_a",
        sid_b: "tests/test_b.py::test_b",
    }
    before = impact_scope(
        tmp_path, source_paths(tmp_path, "demo"), mapping, authority={},
    )
    materialize_scenario_files(coverage)
    after = impact_scope(
        tmp_path, source_paths(tmp_path, "demo"), mapping, authority={},
    )
    assert before["row_digests"] == after["row_digests"]
    assert not plan_has_embedded_rows((coverage / "plan.md").read_text(encoding="utf-8"))
    assert (coverage / "scenarios" / f"{scenario_digest(sid_a)}.md").is_file()


def test_moving_test_file_changes_row_digest(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    spec = tmp_path / "openspec/changes/demo/specs/demo/spec.md"
    spec.parent.mkdir(parents=True)
    spec.write_text("#### Scenario: one\n- **WHEN** a\n- **THEN** b\n", encoding="utf-8")
    body = "def test_one():\n    assert 1\n"
    source = tmp_path / "tests/test_one.py"
    source.parent.mkdir()
    source.write_text(body, encoding="utf-8")
    target_rel = scenario_test_relpath("demo", sid)
    target = tmp_path / target_rel
    target.parent.mkdir(parents=True)
    target.write_text(body, encoding="utf-8")
    coverage = tmp_path / "openspec/changes/demo/scenario-coverage"
    coverage.mkdir(parents=True)
    ensure_machine_receipt_dirs(coverage)
    (coverage / "plan.md").write_text("CHANGE_ID: demo\n", encoding="utf-8")
    plan_receipt_path(coverage).write_text("{}\n", encoding="utf-8")
    (coverage / "scenarios").mkdir()
    (coverage / "scenarios" / f"{scenario_digest(sid)}.md").write_text(
        f"- SCENARIO_ID: {sid}\n", encoding="utf-8",
    )
    trace_matrix_path(coverage).write_text(json.dumps({
        "entries": [
            {"scenario_id": sid, "test_node_id": "tests/test_one.py::test_one"},
        ],
    }), encoding="utf-8")
    old = impact_scope(
        tmp_path, source_paths(tmp_path, "demo"),
        {sid: "tests/test_one.py::test_one"}, authority={},
    )
    new = impact_scope(
        tmp_path, source_paths(tmp_path, "demo"),
        {sid: f"{target_rel}::test_one"}, authority={},
    )
    assert old["row_digests"][sid] != new["row_digests"][sid]


def test_embedded_rows_pause_even_when_catalog_exists(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"SCENARIOS:\n  - SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    plan = coverage / "plan.md"
    plan.write_text(plan.read_text(encoding="utf-8") + f"- SCENARIO_ID: {sid}\n", encoding="utf-8")
    status = inspect_plan_artifacts(tmp_path, "demo", phase="planning")
    assert status["status"] == "BLOCKED"
    assert status["code"] == "COVERAGE_PLAN_EMBEDDED"


def test_historical_plan_still_readable(tmp_path: Path) -> None:
    coverage = _coverage(tmp_path, "- SCENARIO_ID: alpha\n- SCENARIO_ID: beta\n")
    assert read_scenario_ids(coverage, allow_historical=True) == ["alpha", "beta"]
    with pytest.raises(ValueError, match="COVERAGE_PLAN_EMBEDDED"):
        read_scenario_ids(coverage, allow_historical=False)


def test_write_tests_keeps_unmapped_functions(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    source = tmp_path / "tests/test_shared.py"
    source.parent.mkdir()
    source.write_text(
        "def test_one():\n    assert 1\n\n"
        "def test_helper_case():\n    assert 2\n",
        encoding="utf-8",
    )
    trace_matrix_path(coverage).write_text(json.dumps({
        "entries": [{"scenario_id": sid, "test_node_id": "tests/test_shared.py::test_one"}],
    }), encoding="utf-8")
    report = write_tests_for_change(tmp_path, "demo")
    assert sid in report["repair"]
    assert "def test_helper_case" in source.read_text(encoding="utf-8")
    assert not (tmp_path / scenario_test_relpath("demo", sid)).exists()


def test_write_tests_splits_multi_scenario_module(tmp_path: Path) -> None:
    sid_a = "openspec/changes/demo/specs/demo/spec.md::alpha"
    sid_b = "openspec/changes/demo/specs/demo/spec.md::beta"
    coverage = _coverage(
        tmp_path,
        f"- SCENARIO_ID: {sid_a}\n- SCENARIO_ID: {sid_b}\n",
    )
    materialize_scenario_files(coverage)
    source = tmp_path / "tests" / "test_shared.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "VALUE = 1\n\n"
        "def test_alpha():\n"
        "    assert VALUE == 1\n\n"
        "def test_beta():\n"
        "    assert VALUE == 1\n",
        encoding="utf-8",
    )
    trace_matrix_path(coverage).write_text(
        json.dumps({
            "schema_version": 1,
            "entries": [
                {"scenario_id": sid_a, "test_node_id": "tests/test_shared.py::test_alpha"},
                {"scenario_id": sid_b, "test_node_id": "tests/test_shared.py::test_beta"},
            ],
        }),
        encoding="utf-8",
    )
    report = write_tests_for_change(tmp_path, "demo")
    assert set(report["moved"]) == {sid_a, sid_b}
    assert report["repair"] == []
    assert not source.exists()
    path_a = tmp_path / scenario_test_relpath("demo", sid_a)
    path_b = tmp_path / scenario_test_relpath("demo", sid_b)
    assert docstring_satisfies(path_a.read_text(encoding="utf-8"), sid_a)
    assert docstring_satisfies(path_b.read_text(encoding="utf-8"), sid_b)
    matrix = json.loads(trace_matrix_path(coverage).read_text(encoding="utf-8"))
    nodes = {entry["scenario_id"]: entry["test_node_id"] for entry in matrix["entries"]}
    assert nodes[sid_a].startswith(f"{scenario_test_relpath('demo', sid_a)}::")
    assert nodes[sid_b].startswith(f"{scenario_test_relpath('demo', sid_b)}::")


def test_write_tests_moves_single_function_and_documents_it(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    source = tmp_path / "tests/test_only.py"
    source.parent.mkdir()
    source.write_text("def test_only():\n    assert 1\n", encoding="utf-8")
    trace_matrix_path(coverage).write_text(json.dumps({
        "schema_version": 1,
        "entries": [{"scenario_id": sid, "test_node_id": "tests/test_only.py::test_only"}],
    }), encoding="utf-8")
    report = write_tests_for_change(tmp_path, "demo")
    assert report["moved"] == [sid]
    target = tmp_path / scenario_test_relpath("demo", sid)
    text = target.read_text(encoding="utf-8")
    assert "def test_only" in text
    assert docstring_satisfies(text, sid)
    assert not source.exists()
    matrix = json.loads(trace_matrix_path(coverage).read_text(encoding="utf-8"))
    assert matrix["entries"][0]["test_node_id"] == f"{scenario_test_relpath('demo', sid)}::test_only"


def test_pytest_collects_test_prefix_only(tmp_path: Path) -> None:
    import subprocess
    import sys

    folder = tmp_path / "tests/scenarios/demo"
    folder.mkdir(parents=True)
    digest = "a" * 64
    (folder / f"test_{digest}.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (folder / f"{digest}.py").write_text("def test_hidden():\n    assert True\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(tmp_path / "tests")],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    output = proc.stdout + proc.stderr
    assert f"test_{digest}.py::test_ok" in output
    assert output.count("::test_") == 1


@pytest.mark.asyncio
async def test_row_walk_repairs_before_next_scenario_and_aborts_stuck_repair(tmp_path: Path) -> None:
    sid_a = "a"
    sid_b = "b"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid_a}\n- SCENARIO_ID: {sid_b}\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid_a, "test_node_id": "tests/test_a.py::test_a"},
        {"scenario_id": sid_b, "test_node_id": "tests/test_b.py::test_b"},
    ]}), encoding="utf-8")
    order: list[str] = []

    class Audit:
        async def run(self, state):
            sid = state["scenario_row_focus"]
            order.append(f"check:{sid}")
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                "scenario_coverage_answer": {
                    "verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                    "plan_evidence_id": "plan-1",
                    "source_digest": "digest",
                    "row_reviews": [{
                        "scenario_id": sid,
                        "test_node_id": f"tests/test_{sid}.py::test_{sid}",
                        "assessment": "GAP",
                        "evidence": "missing",
                    }],
                    "gaps": [{
                        "scenario_id": sid,
                        "gap_class": "MISSING_OR_WEAK_SCENARIO_TEST",
                        "evidence": "missing",
                    }],
                    "summary": "gap",
                    "block_reason": "",
                    "local_hop_copy_block": "gap",
                },
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    class Repair:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, state):
            self.calls += 1
            sid = state["scenario_coverage_answer"]["gaps"][0]["scenario_id"]
            order.append(f"repair:{sid}")
            if self.calls == 1:
                raise RuntimeError("sdk dropped")
            return {
                "scenario_test_repair_status": "BLOCKED",
                "scenario_test_repair_changed_paths": [],
            }

    repair = Repair()
    state = {
        "repository_path": str(tmp_path),
        "change_id": "demo",
        "run_uuid": "run-1",
    }
    first = await run_scenario_row_walk(state, audit_agent=Audit(), repair_agent=repair)
    assert first["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert order == ["check:a", "repair:a"]
    assert "check:b" not in order
    journal = json.loads(
        row_journal_path(coverage).read_text(encoding="utf-8"),
    )
    assert journal[sid_a]["repair"] == "aborted"
    second = await run_scenario_row_walk(state, audit_agent=Audit(), repair_agent=repair)
    assert repair.calls == 4
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert "ROW_REPAIR_ATTEMPTS_EXCEEDED" in second["scenario_coverage_answer"]["block_reason"]


def _gap_answer(sid: str, assessment: str = "GAP") -> dict:
    answer: dict = {
        "verdict": "SCENARIO_TEST_COVERAGE_FAIL",
        "plan_evidence_id": "plan-1",
        "source_digest": "digest",
        "row_reviews": [{
            "scenario_id": sid,
            "test_node_id": f"tests/test_{sid}.py::test_{sid}",
            "assessment": assessment,
            "evidence": "missing",
            "when_proofs": [1] if assessment == "SUPPORTED" else [],
            "outcome_proofs": [2] if assessment == "SUPPORTED" else [],
        }],
        "gaps": [],
        "summary": "row",
        "block_reason": "spec conflict" if assessment == "CONTRACT_CONFLICT" else "",
        "local_hop_copy_block": "row",
    }
    if assessment == "GAP":
        answer["gaps"] = [{
            "scenario_id": sid,
            "gap_class": "MISSING_OR_WEAK_SCENARIO_TEST",
            "evidence": "missing",
        }]
    if assessment == "CONTRACT_CONFLICT":
        answer["verdict"] = "SCENARIO_TEST_COVERAGE_UNKNOWN"
    if assessment == "SUPPORTED":
        answer["verdict"] = "SCENARIO_TEST_COVERAGE_PASS"
    return answer


@pytest.mark.asyncio
async def test_row_walk_repairs_each_scenario_before_the_next(tmp_path: Path) -> None:
    coverage = _coverage(tmp_path, "- SCENARIO_ID: a\n- SCENARIO_ID: b\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": "a", "test_node_id": "tests/test_a.py::test_a"},
        {"scenario_id": "b", "test_node_id": "tests/test_b.py::test_b"},
    ]}), encoding="utf-8")
    seen: list[str] = []
    after_repair: set[str] = set()

    class Repair:
        async def run(self, state):
            gaps = state["scenario_coverage_answer"]["gaps"]
            assert len(gaps) == 1
            sid = gaps[0]["scenario_id"]
            seen.append(sid)
            after_repair.add(sid)
            return {
                "scenario_test_repair_status": "BLOCKED",
                "scenario_test_repair_changed_paths": [],
            }

    class Audit:
        async def run(self, state):
            sid = state["scenario_row_focus"]
            if sid in after_repair:
                return {
                    "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
                    "scenario_coverage_answer": _gap_answer(sid, "SUPPORTED"),
                    "scenario_coverage_source_snapshot": {"source_digest": "digest"},
                }
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                "scenario_coverage_answer": _gap_answer(sid),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-1"},
        audit_agent=Audit(), repair_agent=Repair(),
    )
    assert seen == ["a", "b"]
    assert result["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert result["scenario_row_walk_settled"] is True


@pytest.mark.asyncio
async def test_contract_conflict_is_repaired_without_skipping_the_next_row(tmp_path: Path) -> None:
    coverage = _coverage(tmp_path, "- SCENARIO_ID: a\n- SCENARIO_ID: b\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": "a", "test_node_id": "tests/test_a.py::test_a"},
        {"scenario_id": "b", "test_node_id": "tests/test_b.py::test_b"},
    ]}), encoding="utf-8")
    repaired: list[str] = []
    after_repair: set[str] = set()

    class Repair:
        async def run(self, state):
            sid = state["scenario_coverage_answer"]["gaps"][0]["scenario_id"]
            repaired.append(sid)
            after_repair.add(sid)
            return {"scenario_test_repair_status": "BLOCKED", "scenario_test_repair_changed_paths": []}

    class Audit:
        async def run(self, state):
            sid = state["scenario_row_focus"]
            if sid in after_repair:
                return {
                    "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
                    "scenario_coverage_answer": _gap_answer(sid, "SUPPORTED"),
                    "scenario_coverage_source_snapshot": {"source_digest": "digest"},
                }
            assessment = "CONTRACT_CONFLICT" if sid == "a" else "GAP"
            verdict = (
                "SCENARIO_TEST_COVERAGE_UNKNOWN"
                if assessment == "CONTRACT_CONFLICT"
                else "SCENARIO_TEST_COVERAGE_FAIL"
            )
            return {
                "scenario_coverage_verdict": verdict,
                "scenario_coverage_answer": _gap_answer(sid, assessment),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-1"},
        audit_agent=Audit(), repair_agent=Repair(),
    )
    assert repaired == ["a", "b"]
    assert result["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"


@pytest.mark.asyncio
async def test_fresh_supported_row_is_reused_until_its_test_changes(tmp_path: Path) -> None:
    sid = "a"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    test = tmp_path / "tests/test_a.py"
    test.parent.mkdir()
    test.write_text("def test_a():\n    assert 1\n", encoding="utf-8")
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid, "test_node_id": "tests/test_a.py::test_a"},
    ]}), encoding="utf-8")

    class Audit:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, state):
            self.calls += 1
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
                "scenario_coverage_answer": _gap_answer(state["scenario_row_focus"], "SUPPORTED"),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    audit = Audit()
    state = {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-1"}
    first = await run_scenario_row_walk(state, audit_agent=audit, repair_agent=Audit())
    assert first["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert audit.calls == 1
    second = await run_scenario_row_walk(state, audit_agent=audit, repair_agent=Audit())
    assert second["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert audit.calls == 1
    test.write_text("def test_a():\n    assert 2\n", encoding="utf-8")
    third = await run_scenario_row_walk(state, audit_agent=audit, repair_agent=Audit())
    assert audit.calls == 2
    assert third["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"


@pytest.mark.asyncio
async def test_walk_commit_includes_test_file_and_matrix(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "agent@test.local"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Agent"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid, "test_node_id": "tests/test_old.py::test_old"},
    ]}), encoding="utf-8")
    target = scenario_test_relpath("demo", sid)

    class Audit:
        async def run(self, state):
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                "scenario_coverage_answer": _gap_answer("openspec/changes/demo/specs/demo/spec.md::one"),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    class Repair:
        async def run(self, state):
            path = tmp_path / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                'def test_only():\n'
                '    """Сценарная проверка.\n'
                '    Смысл: проверка сценария.\n'
                '    Исходные условия: состояние, заданное телом теста.\n'
                '    Действие: выполнение этой test-функции.\n'
                '    Ожидаемый результат: assertions этой функции.\n'
                f'    SCENARIO_ID: {sid}\n'
                '    """\n'
                '    assert 1\n',
                encoding="utf-8",
            )
            return {
                "scenario_test_repair_status": "CANDIDATE_PENDING_VERIFY",
                "scenario_test_repair_changed_paths": [target],
            }

    class RecheckAudit:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, state):
            self.calls += 1
            if self.calls == 1:
                return {
                    "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                    "scenario_coverage_answer": _gap_answer("openspec/changes/demo/specs/demo/spec.md::one"),
                    "scenario_coverage_source_snapshot": {"source_digest": "digest"},
                }
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
                "scenario_coverage_answer": _gap_answer(sid, "SUPPORTED"),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-1"},
        audit_agent=RecheckAudit(),
        repair_agent=Repair(),
    )
    assert "verify_finding_fix" not in result
    green = result["scenario_row_green_commits"]
    assert len(green) == 1
    committed = green[0]["commit_files"]
    assert green[0]["commit_status"] == "COMMITTED"
    assert target in committed
    assert "opsx/receipts/demo/scenario-coverage/test_trace_matrix.json" in committed
    matrix = json.loads(trace_matrix_path(coverage).read_text(encoding="utf-8"))
    assert matrix["entries"][0]["test_node_id"] == f"{target}::test_only"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert green[0]["commit_sha"] == head


def test_combine_unknown_row_blocks_false_pass() -> None:
    order = ["a", "b"]
    supported = {
        "verdict": "SCENARIO_TEST_COVERAGE_PASS",
        "plan_evidence_id": "plan-1",
        "source_digest": "digest",
        "row_reviews": [{
            "scenario_id": "b",
            "test_node_id": "tests/test_b.py::test_b",
            "assessment": "SUPPORTED",
            "evidence": "ok",
            "when_proofs": [1],
            "outcome_proofs": [2],
        }],
        "gaps": [],
        "block_reason": "",
        "local_hop_copy_block": "",
    }
    unknown = {
        "verdict": "SCENARIO_TEST_COVERAGE_UNKNOWN",
        "plan_evidence_id": "plan-1",
        "source_digest": "digest",
        "row_reviews": [],
        "gaps": [],
        "block_reason": "transport timeout",
        "local_hop_copy_block": "",
    }
    answer = _combine("demo", order, {"a": unknown, "b": supported})
    assert answer.verdict == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert len(answer.row_reviews) == 1


@pytest.mark.asyncio
async def test_row_walk_reconciles_orphan_started_without_repair_uuid(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    journal_path = row_journal_path(coverage)
    journal_path.write_text(
        json.dumps({
            sid: {"token": "stale-token", "repair": "started"},
        }),
        encoding="utf-8",
    )
    calls = 0
    after_repair: set[str] = set()

    class Audit:
        async def run(self, state):
            if sid in after_repair:
                return {
                    "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
                    "scenario_coverage_answer": _gap_answer(sid, "SUPPORTED"),
                    "scenario_coverage_source_snapshot": {"source_digest": "digest"},
                }
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                "scenario_coverage_answer": _gap_answer(sid),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    class Repair:
        async def run(self, state):
            nonlocal calls
            calls += 1
            after_repair.add(sid)
            return {
                "scenario_test_repair_status": "BLOCKED",
                "scenario_test_repair_changed_paths": [],
            }

    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-2"},
        audit_agent=Audit(),
        repair_agent=Repair(),
    )
    assert calls == 1
    assert result["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert result["scenario_row_walk_settled"] is True
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal[sid]["repair"] == "finished"


@pytest.mark.asyncio
async def test_row_walk_reconciles_started_with_finished_repair_meta(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    old_run = "run-old"
    dialog = (
        tmp_path / "opsx/opsx_runtime/run_dialogs/demo" / old_run / "scenario_test_repair"
    )
    dialog.mkdir(parents=True)
    (dialog / "meta.json").write_text(
        json.dumps({"sdk_status": "finished", "run_uuid": old_run}),
        encoding="utf-8",
    )
    journal_path = row_journal_path(coverage)
    journal_path.write_text(
        json.dumps({
            sid: {
                "token": "stale-token",
                "repair": "started",
                "repair_run_uuid": old_run,
            },
        }),
        encoding="utf-8",
    )
    repair_calls = 0
    after_repair: set[str] = set()

    class Audit:
        async def run(self, state):
            if sid in after_repair:
                return {
                    "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
                    "scenario_coverage_answer": _gap_answer(sid, "SUPPORTED"),
                    "scenario_coverage_source_snapshot": {"source_digest": "digest"},
                }
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                "scenario_coverage_answer": _gap_answer(sid),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    class Repair:
        async def run(self, state):
            nonlocal repair_calls
            repair_calls += 1
            after_repair.add(sid)
            return {
                "scenario_test_repair_status": "BLOCKED",
                "scenario_test_repair_changed_paths": [],
            }

    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-new"},
        audit_agent=Audit(),
        repair_agent=Repair(),
    )
    assert repair_calls == 1
    assert result["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_PASS"
    assert result["scenario_row_walk_settled"] is True


@pytest.mark.asyncio
async def test_row_walk_pauses_when_repair_sdk_still_running(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    active_run = "run-active"
    dialog = (
        tmp_path / "opsx/opsx_runtime/run_dialogs/demo" / active_run / "scenario_test_repair"
    )
    dialog.mkdir(parents=True)
    (dialog / "meta.json").write_text(
        json.dumps({"sdk_status": "running", "run_uuid": active_run}),
        encoding="utf-8",
    )
    row_journal_path(coverage).write_text(
        json.dumps({
            sid: {
                "token": "t",
                "repair": "started",
                "repair_run_uuid": active_run,
            },
        }),
        encoding="utf-8",
    )
    repair_calls = 0

    class Audit:
        async def run(self, state):
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                "scenario_coverage_answer": _gap_answer(sid),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    class Repair:
        async def run(self, state):
            nonlocal repair_calls
            repair_calls += 1
            return {
                "scenario_test_repair_status": "BLOCKED",
                "scenario_test_repair_changed_paths": [],
            }

    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-new"},
        audit_agent=Audit(),
        repair_agent=Repair(),
    )
    assert repair_calls == 0
    assert result["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert "REPAIR_STILL_ACTIVE" in result["scenario_coverage_answer"]["block_reason"]


def test_check_coverage_requires_canonical_test_files(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid, "test_node_id": "tests/missing.py::test_x"},
    ]}), encoding="utf-8")
    problems = check_coverage(coverage, repo=tmp_path)
    assert any("SCENARIO_TEST_FILE_MISSING" in item for item in problems)
    target = scenario_test_relpath("demo", sid)
    path = tmp_path / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'def test_only():\n'
        '    """Сценарная проверка.\n'
        '    Смысл: проверка сценария.\n'
        '    Исходные условия: состояние, заданное телом теста.\n'
        '    Действие: выполнение этой test-функции.\n'
        '    Ожидаемый результат: assertions этой функции.\n'
        f'    SCENARIO_ID: {sid}\n'
        '    """\n'
        '    assert 1\n',
        encoding="utf-8",
    )
    problems = check_coverage(coverage, repo=tmp_path)
    assert any("SCENARIO_TEST_MAPPING_NOT_CANONICAL" in item for item in problems)


def test_write_tests_keeps_source_when_module_still_imported(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    package = tmp_path / "tests" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    source = package / "test_case.py"
    source.write_text("def test_only():\n    assert 1\n", encoding="utf-8")
    rel = source.relative_to(tmp_path).as_posix()
    (tmp_path / "tests" / "importer.py").write_text(
        f"import {rel[:-3].replace('/', '.')}\n",
        encoding="utf-8",
    )
    target = scenario_test_relpath("demo", sid)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid, "test_node_id": f"{rel}::test_only"},
    ]}), encoding="utf-8")
    report = write_tests_for_change(tmp_path, "demo")
    assert sid in report["repair"]
    assert not (tmp_path / target).is_file()
    assert source.is_file()


def test_repo_scenario_coverage_with_matrix_passes_canonical_check() -> None:
    from agents.common.scenario_migrate import check_coverage, iter_coverage_dirs

    repo = Path(__file__).resolve().parents[5]
    failures: list[str] = []
    for coverage in iter_coverage_dirs(repo):
        label = coverage.relative_to(repo).as_posix()
        if not trace_matrix_path(coverage).is_file():
            failures.append(f"{label}: COVERAGE_TRACE_MATRIX_MISSING")
            continue
        problems = check_coverage(coverage, repo=repo, require_canonical_tests=True)
        if problems:
            failures.append(f"{label}: {', '.join(problems)}")
    assert not failures


def test_check_coverage_allows_missing_canonical_tests_when_not_required(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid, "test_node_id": "tests/old.py::test_old"},
    ]}), encoding="utf-8")
    assert not check_coverage(coverage, repo=tmp_path, require_canonical_tests=False)
    assert check_coverage(coverage, repo=tmp_path, require_canonical_tests=True)


def test_bind_matrix_appends_missing_entry(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": []}), encoding="utf-8")
    target = scenario_test_relpath("demo", sid)
    path = tmp_path / target
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'def test_only():\n'
        '    """Сценарная проверка.\n'
        '    Смысл: проверка сценария.\n'
        '    Исходные условия: состояние, заданное телом теста.\n'
        '    Действие: выполнение этой test-функции.\n'
        '    Ожидаемый результат: assertions этой функции.\n'
        f'    SCENARIO_ID: {sid}\n'
        '    """\n'
        '    assert 1\n',
        encoding="utf-8",
    )
    assert _bind_matrix(tmp_path, "demo", sid) is not None
    matrix = json.loads(trace_matrix_path(coverage).read_text(encoding="utf-8"))
    assert matrix["entries"][0]["test_node_id"] == f"{target}::test_only"


def test_collect_green_row_commit_paths_includes_deleted_tracked(tmp_path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    rel = "tests/will_delete.py"
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", rel], cwd=tmp_path, check=True, capture_output=True)
    path.unlink()
    paths = collect_green_row_commit_paths(
        tmp_path, "demo", "sid", extra_paths=[rel],
    )
    assert rel in paths


@pytest.mark.asyncio
async def test_row_walk_pauses_when_green_commit_fails(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    from agents.common.commit_agent import CommitAgent

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "a@b.local"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Agent"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid, "test_node_id": "tests/test_x.py::test_x"},
    ]}), encoding="utf-8")

    class Audit:
        async def run(self, state):
            focus = state["scenario_row_focus"]
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
                "scenario_coverage_answer": _gap_answer(focus, "SUPPORTED"),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    async def fail_commit(self, *_args, **_kwargs):
        return {
            "commit_status": "FAILED",
            "error": "index.lock",
            "commit_files": [],
            "commit_sha": None,
        }

    monkeypatch.setattr(CommitAgent, "run", fail_commit)
    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-1"},
        audit_agent=Audit(),
        repair_agent=Audit(),
    )
    assert result["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert "ROW_COMMIT_FAILED" in result["scenario_coverage_answer"]["block_reason"]


@pytest.mark.asyncio
async def test_row_walk_retries_repair_on_same_token_gap_recheck(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid, "test_node_id": "tests/test_x.py::test_x"},
    ]}), encoding="utf-8")
    row_journal_path(coverage).write_text(
        json.dumps({
            sid: {
                "token": "stale-token",
                "repair": "finished",
                "repair_attempts": 1,
                "recheck": _gap_answer(sid),
            },
        }),
        encoding="utf-8",
    )
    repair_calls = 0

    class Audit:
        async def run(self, state):
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                "scenario_coverage_answer": _gap_answer(sid),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    class Repair:
        async def run(self, state):
            nonlocal repair_calls
            repair_calls += 1
            return {
                "scenario_test_repair_status": "BLOCKED",
                "scenario_test_repair_changed_paths": [],
            }

    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-1"},
        audit_agent=Audit(),
        repair_agent=Repair(),
    )
    assert repair_calls == 2
    assert result["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert "ROW_REPAIR_ATTEMPTS_EXCEEDED" in result["scenario_coverage_answer"]["block_reason"]


@pytest.mark.asyncio
async def test_row_walk_pauses_after_three_repairs_without_supported(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid, "test_node_id": "tests/test_x.py::test_x"},
    ]}), encoding="utf-8")
    repair_calls = 0

    class Audit:
        async def run(self, state):
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                "scenario_coverage_answer": _gap_answer(sid),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    class Repair:
        async def run(self, state):
            nonlocal repair_calls
            repair_calls += 1
            return {
                "scenario_test_repair_status": "BLOCKED",
                "scenario_test_repair_changed_paths": [],
            }

    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-1"},
        audit_agent=Audit(),
        repair_agent=Repair(),
    )
    assert repair_calls == 3
    assert "ROW_REPAIR_ATTEMPTS_EXCEEDED" in result["scenario_coverage_answer"]["block_reason"]


@pytest.mark.asyncio
async def test_row_walk_pauses_after_max_repair_attempts(tmp_path: Path) -> None:
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid, "test_node_id": "tests/test_x.py::test_x"},
    ]}), encoding="utf-8")
    row_journal_path(coverage).write_text(
        json.dumps({
            sid: {
                "repair_attempts": 3,
                "repair": "finished",
                "repair_outcome": "BLOCKED",
                "token": "stale",
                "recheck": _gap_answer(sid),
            },
        }),
        encoding="utf-8",
    )
    repair_calls = 0

    class Audit:
        async def run(self, state):
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                "scenario_coverage_answer": _gap_answer(sid),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    class Repair:
        async def run(self, state):
            nonlocal repair_calls
            repair_calls += 1
            return {
                "scenario_test_repair_status": "BLOCKED",
                "scenario_test_repair_changed_paths": [],
            }

    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-1"},
        audit_agent=Audit(),
        repair_agent=Repair(),
    )
    assert result["scenario_coverage_verdict"] == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    assert "ROW_REPAIR_ATTEMPTS_EXCEEDED" in result["scenario_coverage_answer"]["block_reason"]
    assert repair_calls == 0


def test_row_walk_journal_reset_repair_attempts(tmp_path: Path) -> None:
    (tmp_path / "opsx").mkdir()
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    journal_path = row_journal_path(coverage)
    journal_path.write_text(
        json.dumps({sid: {"repair_attempts": 3, "repair": "finished"}}),
        encoding="utf-8",
    )
    n = reset_row_walk_journal_repair_attempts(tmp_path, "demo")
    assert n == 1
    journal = RowWalkJournal(journal_path)
    assert journal.repair_attempts(sid) == 0


@pytest.mark.asyncio
async def test_row_walk_resumes_repairs_after_journal_reset(tmp_path: Path) -> None:
    (tmp_path / "opsx").mkdir()
    sid = "openspec/changes/demo/specs/demo/spec.md::one"
    coverage = _coverage(tmp_path, f"- SCENARIO_ID: {sid}\n")
    materialize_scenario_files(coverage)
    trace_matrix_path(coverage).write_text(json.dumps({"entries": [
        {"scenario_id": sid, "test_node_id": "tests/test_x.py::test_x"},
    ]}), encoding="utf-8")
    journal_path = row_journal_path(coverage)
    journal_path.write_text(
        json.dumps({
            sid: {
                "repair_attempts": 3,
                "repair": "finished",
                "repair_outcome": "BLOCKED",
                "token": "stale",
                "recheck": _gap_answer(sid),
            },
        }),
        encoding="utf-8",
    )
    reset_row_walk_journal_repair_attempts(tmp_path, "demo")
    repair_calls = 0

    class Audit:
        async def run(self, state):
            return {
                "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
                "scenario_coverage_answer": _gap_answer(sid),
                "scenario_coverage_source_snapshot": {"source_digest": "digest"},
            }

    class Repair:
        async def run(self, state):
            nonlocal repair_calls
            repair_calls += 1
            return {
                "scenario_test_repair_status": "BLOCKED",
                "scenario_test_repair_changed_paths": [],
            }

    result = await run_scenario_row_walk(
        {"repository_path": str(tmp_path), "change_id": "demo", "run_uuid": "run-1"},
        audit_agent=Audit(),
        repair_agent=Repair(),
    )
    assert repair_calls == 3
    assert "ROW_REPAIR_ATTEMPTS_EXCEEDED" in result["scenario_coverage_answer"]["block_reason"]
