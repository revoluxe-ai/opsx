"""Тесты mypy activation registry и audit."""

from __future__ import annotations

from pathlib import Path

from agents.verify.coverage_activation.mypy_static import (
    audit_mypy_paths,
    build_mypy_activation_plan,
    mypy_path_in_product_scope,
    register_mypy_paths,
)


def test_production_under_src_stroy_in_mypy_scope(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "mypy.ini").write_text(
        "[mypy]\nfiles = src/stroy\nexclude = (^|/)(tests)(/|$)\n",
        encoding="utf-8",
    )
    assert mypy_path_in_product_scope(root, "src/stroy/foo.py")
    assert not mypy_path_in_product_scope(root, "tests/unit/test_x.py")


def test_audit_marks_tests_needs_register(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "mypy.ini").write_text(
        "[mypy]\nfiles = src/stroy\nexclude = (^|/)(tests)(/|$)\n",
        encoding="utf-8",
    )
    audit = audit_mypy_paths(root, ("tests/unit/test_a.py", "src/stroy/a.py"))
    plan = build_mypy_activation_plan(audit)
    assert "tests/unit/test_a.py" in plan["register"]
    assert "src/stroy/a.py" not in plan["register"]


def test_register_mypy_paths_dedupes(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    register_mypy_paths(root, ["tests/a.py"])
    register_mypy_paths(root, ["tests/a.py", "tests/b.py"])
    from agents.verify.coverage_activation.mypy_static import load_registered_mypy_paths

    assert load_registered_mypy_paths(root) == ["tests/a.py", "tests/b.py"]
