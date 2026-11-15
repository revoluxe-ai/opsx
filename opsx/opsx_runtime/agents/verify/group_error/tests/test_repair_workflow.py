"""Тесты ``classify_repair_workflow``."""

from __future__ import annotations

from agents.verify.group_error.repair_workflow import classify_repair_workflow


def test_classify_pytest():
    assert (
        classify_repair_workflow(
            source_tool="pytest",
            observation="FAILED tests/test_x.py::test_y - AssertionError",
        )
        == "WF_PYTEST_FAILURE"
    )


def test_classify_pyright_lint():
    assert (
        classify_repair_workflow(
            source_tool="pyright",
            observation='x.py:1 - error: reportUnusedCallResult',
        )
        == "WF_PYRIGHT_LINT"
    )


def test_classify_mypy_import():
    assert (
        classify_repair_workflow(
            source_tool="mypy",
            observation="mod.py:1: import-untyped",
        )
        == "WF_MYPY_IMPORT"
    )
