"""Контракт entrypoint: одна CLI-папка ``tests/`` → полный runtime tree."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_runtime_tests_conftest():
    conftest_path = Path(__file__).resolve().parent / "conftest.py"
    spec = importlib.util.spec_from_file_location(
        "opsx_runtime_tests_conftest",
        conftest_path,
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_runtime_entry_paths_exist() -> None:
    tests = Path(__file__).resolve().parent
    root = tests.parent
    assert (root.parent / "opsx").is_dir()
    assert (root / "workflows").is_dir()
    assert (root / "agents").is_dir()


def test_entrypoint_detector_matches_single_tests_arg() -> None:
    mod = _load_runtime_tests_conftest()
    tests = Path(__file__).resolve().parent
    root = tests.parent
    assert mod._is_runtime_tests_entrypoint([str(tests)])
    assert not mod._is_runtime_tests_entrypoint(
        [str(tests), str(root / "workflows")]
    )
    assert not mod._is_runtime_tests_entrypoint([str(root)])
