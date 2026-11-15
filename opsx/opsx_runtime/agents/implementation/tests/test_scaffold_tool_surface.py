"""apply_scaffold не получает grep/glob/shell и не сканирует корень репозитория."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.implementation.apply_scaffold.tool_surface import (
    INPUT_SCOPE_INCOMPLETE,
    SCAFFOLD_TOOLS,
    search_in_scope,
    work_scope_from_design,
)


def test_scaffold_tools_have_no_discovery() -> None:
    assert "grep" not in SCAFFOLD_TOOLS
    assert "glob" not in SCAFFOLD_TOOLS
    assert "shell" not in SCAFFOLD_TOOLS
    assert "ls" not in SCAFFOLD_TOOLS
    assert SCAFFOLD_TOOLS == ("read", "edit")


def test_work_scope_comes_from_design_slice() -> None:
    design = {
        "slices": [
            {
                "slice_id": "SLICE-001",
                "scaffold_contract": {
                    "required_surfaces": [{"path": "src/visual/cache.py"}],
                    "allowed_scaffold_writes": ["src/visual/"],
                },
                "expected_product_write_surfaces": ["src/visual/cache.py"],
            }
        ]
    }
    assert work_scope_from_design(design, "SLICE-001") == [
        "src/visual/cache.py",
        "src/visual/",
    ]


def test_missing_work_scope_is_incomplete() -> None:
    assert work_scope_from_design({"slices": []}, "SLICE-001") == []
    assert work_scope_from_design(None, "SLICE-001") == []
    assert INPUT_SCOPE_INCOMPLETE == "INPUT_SCOPE_INCOMPLETE"


def test_search_in_scope_stays_inside_roots(tmp_path: Path) -> None:
    root = tmp_path
    inside = root / "src" / "visual"
    inside.mkdir(parents=True)
    (inside / "cache.py").write_text("x", encoding="utf-8")
    outside = root / "other"
    outside.mkdir()
    (outside / "secret.py").write_text("y", encoding="utf-8")
    found = search_in_scope(root, ["src/visual"], "*.py")
    assert found == ["src/visual/cache.py"]


def test_search_rejects_repository_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        search_in_scope(tmp_path, ["."], "*")
