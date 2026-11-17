"""Контракт: workflow defer — маркер, gate, без subprocess pytest."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agents.repair.scenario_test_repair.live_checks import run_focused_checks
from agents.repair.scenario_test_repair.workflow_pytest_defer import (
    _discover_mapped_node,
    _ensure_pytest_import,
    ensure_workflow_deferred_on_node,
    repair_pytest_gate_machine_gap,
    repair_pytest_gate_passed,
    validate_mapped_workflow_deferred_marker,
    workflow_defer_blocks_supported_verdict,
)
from agents.common.scenario_files import scenario_digest, scenario_test_relpath
from agents.verify.scenario_coverage_verify.row_cycle import _row_audit_supported


def _scenario_body(marker: bool) -> str:
    deco = "@pytest.mark.workflow_deferred\n" if marker else ""
    return (
        "import pytest\n\n"
        f"{deco}"
        "@pytest.mark.asyncio\n"
        "async def test_hermetic_seam() -> None:\n"
        '    """SCENARIO_ID: openspec/changes/visual/specs/demo/spec.md::Example"""\n'
        "    assert True\n"
    )


NODE = "tests/scenarios/visual/test_defer_contract.py::test_hermetic_seam"
SID = "openspec/changes/visual/specs/demo/spec.md::Example"
_RUNTIME = Path(__file__).resolve().parents[4]
_REPAIR_HOP_SOURCES = (
    _RUNTIME / "agents" / "repair" / "scenario_test_repair" / "agent.py",
    _RUNTIME / "agents" / "repair" / "scenario_test_repair" / "live_checks.py",
    _RUNTIME / "agents" / "repair" / "scenario_test_repair" / "mapped_surface.py",
    _RUNTIME / "workflows" / "verify" / "graph.py",
)
_FORBIDDEN_PYTEST_INVOCATIONS = (
    "run_pytest_execute",
    "run_pytest_execute_sync",
)


def _supported_patch(node: str, *, assessment: str = "SUPPORTED") -> dict:
    return {
        "scenario_coverage_answer": {
            "verdict": "SCENARIO_TEST_COVERAGE_PASS" if assessment == "SUPPORTED" else "SCENARIO_TEST_COVERAGE_FAIL",
            "row_reviews": [{
                "scenario_id": SID,
                "test_node_id": node,
                "assessment": assessment,
                "evidence": "llm",
            }],
        },
    }


def test_repair_hop_modules_do_not_invoke_run_pytest_execute() -> None:
    """Регрессия: repair graph-hop не ссылается на pytest execute runner."""
    for path in _REPAIR_HOP_SOURCES:
        text = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN_PYTEST_INVOCATIONS:
            assert token not in text, f"{path.name} must not reference {token}"


@pytest.mark.asyncio
async def test_focused_checks_skips_pytest_for_unit_runnable_node(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit mapped node — subprocess pytest не создаётся (exit_code None)."""
    repo = tmp_path
    change = "visual"
    unit_dir = repo / "tests" / "unit" / "visual"
    unit_dir.mkdir(parents=True)
    (unit_dir / "test_runnable.py").write_text(
        "def test_smoke() -> None:\n    assert True\n",
        encoding="utf-8",
    )
    unit_node = "tests/unit/visual/test_runnable.py::test_smoke"
    monkeypatch.setattr(
        "agents.repair.scenario_test_repair.live_checks.mapped_test_nodes",
        lambda *_args, **_kwargs: [unit_node],
    )
    run_async = AsyncMock()
    monkeypatch.setattr(
        "workflows.verify.pytest_execute.invoke.run_pytest_execute",
        run_async,
    )

    receipt = await run_focused_checks(
        repo,
        change,
        [SID],
        execution_id="repair-hop-no-pytest",
    )

    assert receipt["status"] == "DEFERRED"
    assert receipt.get("exit_code") is None
    assert receipt.get("reason") == "WORKFLOW_REPAIR_PYTEST_SKIPPED"
    run_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_focused_checks_skips_pytest_for_requires_postgres_tier_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``tests/scenarios/*`` tier — subprocess pytest не создаётся (exit_code None)."""
    repo = tmp_path
    change = "visual"
    scenario_dir = repo / "tests" / "scenarios" / change
    scenario_dir.mkdir(parents=True)
    test_file = scenario_dir / "test_defer_contract.py"
    test_file.write_text(_scenario_body(marker=True), encoding="utf-8")
    monkeypatch.setattr(
        "agents.repair.scenario_test_repair.live_checks.mapped_test_nodes",
        lambda *_args, **_kwargs: [NODE],
    )
    run_async = AsyncMock()
    monkeypatch.setattr(
        "workflows.verify.pytest_execute.invoke.run_pytest_execute",
        run_async,
    )

    receipt = await run_focused_checks(
        repo,
        change,
        [SID],
        execution_id="defer-contract-hop",
    )

    assert receipt["status"] == "DEFERRED"
    assert receipt.get("exit_code") is None
    assert receipt.get("reason") == "WORKFLOW_SCENARIO_NOT_EXECUTABLE"
    run_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_focused_checks_blocks_when_marker_missing_on_tier_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path
    change = "visual"
    scenario_dir = repo / "tests" / "scenarios" / change
    scenario_dir.mkdir(parents=True)
    (scenario_dir / "test_defer_contract.py").write_text(
        _scenario_body(marker=False), encoding="utf-8",
    )
    monkeypatch.setattr(
        "agents.repair.scenario_test_repair.live_checks.mapped_test_nodes",
        lambda *_args, **_kwargs: [NODE],
    )
    run_async = AsyncMock()
    monkeypatch.setattr(
        "workflows.verify.pytest_execute.invoke.run_pytest_execute",
        run_async,
    )

    receipt = await run_focused_checks(repo, change, [SID], execution_id="hop")

    assert receipt["status"] == "BLOCKED"
    assert receipt.get("exit_code") is None
    assert "WORKFLOW_DEFERRED_MARKER_MISSING" in str(receipt.get("reason") or "")
    run_async.assert_not_awaited()
    assert _row_audit_supported(repo, _supported_patch(NODE)) is False


def test_ensure_workflow_deferred_on_node_writes_marker(tmp_path: Path) -> None:
    repo = tmp_path
    path = repo / "tests" / "scenarios" / "visual" / "test_defer_contract.py"
    path.parent.mkdir(parents=True)
    path.write_text(_scenario_body(marker=False), encoding="utf-8")
    changed = ensure_workflow_deferred_on_node(repo, NODE)
    assert changed is True
    source = path.read_text(encoding="utf-8")
    assert validate_mapped_workflow_deferred_marker(repo, NODE) is None
    assert "@pytest.mark.workflow_deferred" in source


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("PASS", True),
        ("DEFERRED", False),
        ("FAIL", False),
        ("BLOCKED", False),
    ],
)
def test_repair_pytest_gate_passed_only_pass(status: str, expected: bool) -> None:
    assert repair_pytest_gate_passed({"status": status, "exit_code": 0 if status == "PASS" else None}) is expected


@pytest.mark.parametrize(
    ("status", "machine_gap"),
    [
        ("PASS", False),
        ("DEFERRED", False),
        ("BLOCKED", True),
        ("FAIL", True),
    ],
)
def test_repair_pytest_gate_machine_gap_defers_to_recheck_not_gap(
    status: str, machine_gap: bool,
) -> None:
    receipt = {"status": status, "exit_code": None, "reason": "x"}
    assert repair_pytest_gate_machine_gap(receipt) is machine_gap


def test_row_audit_supported_accepts_deferred_node_with_marker(tmp_path: Path) -> None:
    repo = tmp_path
    path = repo / "tests" / "scenarios" / "visual" / "test_defer_contract.py"
    path.parent.mkdir(parents=True)
    path.write_text(_scenario_body(marker=True), encoding="utf-8")
    assert workflow_defer_blocks_supported_verdict(repo, NODE) is False
    assert _row_audit_supported(repo, _supported_patch(NODE)) is True


def test_row_audit_supported_rejects_gap_even_with_marker(tmp_path: Path) -> None:
    repo = tmp_path
    path = repo / "tests" / "scenarios" / "visual" / "test_defer_contract.py"
    path.parent.mkdir(parents=True)
    path.write_text(_scenario_body(marker=True), encoding="utf-8")
    assert _row_audit_supported(repo, _supported_patch(NODE, assessment="GAP")) is False


def test_workflow_pytest_defer_import_without_tests_package() -> None:
    """Импорт модуля defer не тянет ``tests.pytest_suite_tiers`` (только opsx_runtime на PATH)."""
    runtime_root = Path(__file__).resolve().parents[4]
    env = {**os.environ, "PYTHONPATH": str(runtime_root)}
    script = (
        "import sys; "
        "import agents.repair.scenario_test_repair.workflow_pytest_defer; "
        "assert 'tests.pytest_suite_tiers' not in sys.modules, list(sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=runtime_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


def test_ensure_pytest_import_after_future_and_idempotent(tmp_path: Path) -> None:
    """Регрессия: import pytest не обгоняет from __future__; повтор — no-op."""
    source = (
        '"""Visual change canonical scenario seam."""\n'
        "from __future__ import annotations\n\n"
        "def test_x() -> None:\n"
        "    assert True\n"
    )
    once = _ensure_pytest_import(source)
    compile(once, "t.py", "exec")
    assert once.index("from __future__") < once.index("import pytest")
    twice = _ensure_pytest_import(once)
    assert twice == once
    blank_before_future = (
        '"""doc."""\n'
        "\n"
        "# encoding kept above future\n"
        "from __future__ import annotations\n"
        "\n"
        "def test_x() -> None:\n"
        "    assert True\n"
    )
    placed = _ensure_pytest_import(blank_before_future)
    compile(placed, "t.py", "exec")
    assert placed.index("from __future__") < placed.index("import pytest")


def test_ensure_workflow_deferred_idempotent_with_future_import(tmp_path: Path) -> None:
    repo = tmp_path
    path = repo / "tests" / "scenarios" / "visual" / "test_future.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        '"""doc."""\n'
        "from __future__ import annotations\n\n"
        "@pytest.mark.asyncio\n"
        "async def test_future() -> None:\n"
        "    assert True\n",
        encoding="utf-8",
    )
    node = "tests/scenarios/visual/test_future.py::test_future"
    assert ensure_workflow_deferred_on_node(repo, node) is True
    first = path.read_text(encoding="utf-8")
    compile(first, str(path), "exec")
    assert ensure_workflow_deferred_on_node(repo, node) is False
    assert path.read_text(encoding="utf-8") == first


def test_workflow_defer_blocks_supported_only_without_marker(tmp_path: Path) -> None:
    repo = tmp_path
    missing = repo / "tests" / "scenarios" / "visual" / "test_no_marker.py"
    missing.parent.mkdir(parents=True)
    missing.write_text(_scenario_body(marker=False), encoding="utf-8")
    node_missing = "tests/scenarios/visual/test_no_marker.py::test_hermetic_seam"
    assert workflow_defer_blocks_supported_verdict(repo, node_missing) is True
    assert _row_audit_supported(repo, _supported_patch(node_missing)) is False


def test_discover_mapped_node_without_trace_matrix_uses_canonical_test(
    tmp_path: Path,
) -> None:
    """До scenario_trace_materialize матрицы нет — node id из canonical test_*.py."""
    repo = tmp_path
    change_id = "visual"
    scenario_id = SID
    rel = scenario_test_relpath(change_id, scenario_id)
    path = repo / rel
    path.parent.mkdir(parents=True)
    path.write_text(_scenario_body(marker=False), encoding="utf-8")
    assert not (repo / "opsx" / "receipts" / change_id / "scenario-coverage").exists()
    node = _discover_mapped_node(repo, change_id, scenario_id)
    assert node == f"{rel}::test_hermetic_seam"
