"""Unit-тесты MCP ``run_pytest``: валидация node id и отказ от non-node-id."""

from __future__ import annotations

import pytest

from agents.verify.repair.run_pytest_tool import validate_repair_hop_pytest_node_id


def test_validate_rejects_non_tests_node_id() -> None:
    with pytest.raises(ValueError, match="tests/"):
        validate_repair_hop_pytest_node_id("src/foo.py::test_bar")


def test_validate_rejects_shell_metacharacters() -> None:
    with pytest.raises(ValueError, match="metachar"):
        validate_repair_hop_pytest_node_id("tests/a.py::test_x; rm -rf /")


def test_validate_accepts_single_tests_node_id() -> None:
    node = validate_repair_hop_pytest_node_id(
        "tests/unit/foo/test_bar.py::test_smoke",
    )
    assert node == "tests/unit/foo/test_bar.py::test_smoke"


def test_validate_accepts_tests_module_path_without_node_suffix() -> None:
    node = validate_repair_hop_pytest_node_id("tests/unit/foo/test_bar.py")
    assert node == "tests/unit/foo/test_bar.py"


def test_validate_resolves_unique_basename_under_tests(tmp_path) -> None:
    module = tmp_path / "tests" / "unit" / "test_only_one.py"
    module.parent.mkdir(parents=True)
    module.write_text("def test_x(): pass\n", encoding="utf-8")
    node = validate_repair_hop_pytest_node_id(
        "test_only_one.py",
        repo_root=tmp_path,
    )
    assert node == "tests/unit/test_only_one.py"


def test_validate_rejects_ambiguous_basename(tmp_path) -> None:
    for sub in ("tests/a", "tests/b"):
        path = tmp_path / sub / "dup.py"
        path.parent.mkdir(parents=True)
        path.write_text("def test_x(): pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        validate_repair_hop_pytest_node_id("dup.py", repo_root=tmp_path)
