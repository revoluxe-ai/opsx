"""Регрессии CI-контракта Temporal: guard должен падать при пропаже обязательных тестов."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from unittest.mock import patch

from ci_contract_guard import (
    MIN_TOTAL_COLLECTED,
    CollectionReport,
    REQUIRED_NODE_SUFFIXES,
    collect_temporal_p0_node_ids,
    collection_process_errors,
    main,
    verify_collection,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_verify_collection_fails_when_required_node_missing():
    """AC-A1: удаление обязательного node id из коллекции ломает guard."""
    fake_nodes = (
        "tools/temporal/tests/test_temporal_workflow.py::test_temporal_workflow_exposes_granular_activities",
    )
    errors = verify_collection(fake_nodes, min_total=1)
    assert errors, "guard must fail when most required tests are absent"
    assert any("required" in e.lower() for e in errors)


def test_verify_collection_fails_on_empty_collection():
    errors = verify_collection(())
    assert any("zero tests" in e for e in errors)


def test_collection_nonzero_exit_fails_even_with_partial_nodes():
    """Регрессия: частичная коллекция при exit_code != 0 не должна проходить guard."""
    fake_nodes = tuple(
        f"tools/temporal/tests/test_workflow_repair_transition_contract.py::fake_{i}"
        for i in range(MIN_TOTAL_COLLECTED)
    )
    report = CollectionReport(node_ids=fake_nodes, exit_code=2, stderr="simulated collect error")
    assert collection_process_errors(report)
    with patch("ci_contract_guard.collect_temporal_p0_node_ids", return_value=report):
        assert main(["verify-collection"]) != 0


def test_verify_collection_fails_below_minimum_count():
    nodes = tuple(
        f"tools/temporal/tests/test_workflow_repair_transition_contract.py::fake_{i}"
        for i in range(MIN_TOTAL_COLLECTED - 1)
    )
    errors = verify_collection(nodes)
    assert any("minimum required" in e for e in errors)


def test_live_collection_includes_workflow_environment_tests():
    """Локально/в CI: collect-only должен видеть WorkflowEnvironment-тесты."""
    report = collect_temporal_p0_node_ids()
    assert report.exit_code == 0, report.stderr
    errors = verify_collection(report.node_ids)
    assert not errors, errors
    wf_nodes = [n for n in report.node_ids if "test_temporal_workflow.py" in n]
    assert wf_nodes, "WorkflowEnvironment module must contribute tests"


def test_main_verify_collection_exit_zero_in_repo():
    code = main(["verify-collection"])
    assert code == 0


def test_negative_fixture_removed_file_fails_guard(tmp_path: Path):
    """AC-A1: симуляция отсутствия файла контракта → ненулевой exit collect."""
    missing = _REPO_ROOT / "tools/temporal/tests/test_workflow_repair_transition_contract.py"
    assert missing.is_file()
    # Не удаляем файл в репо — проверяем verify на искусственно урезанном наборе.
    trimmed = tuple(
        n
        for n in collect_temporal_p0_node_ids().node_ids
        if "test_workflow_repair_transition_contract.py" not in n
    )
    errors = verify_collection(trimmed)
    assert any(
        "test_workflow_repair_transition_contract" in e or "required" in e.lower()
        for e in errors
    )
