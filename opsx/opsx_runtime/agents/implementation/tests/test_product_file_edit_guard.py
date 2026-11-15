"""Регрессия: откат обнуления крупного product-файла на edit completed."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.config import ReadScopeViolation
from agents.implementation.product_file_edit_guard import enforce_product_file_edit_guard
_HANDLER_REL = "src/stroy/consultation/invest_yalta/handler.py"


def test_edit_guard_rolls_back_truncate_to_empty(tmp_path: Path) -> None:
    """2973-line wipe → rollback + ReadScopeViolation."""
    (tmp_path / "opsx").mkdir()
    target = tmp_path / _HANDLER_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    prior = "line\n" * 500
    _ = target.write_text(prior, encoding="utf-8")
    snapshots: dict[str, str] = {}
    args = {"path": _HANDLER_REL}
    enforce_product_file_edit_guard(
        "edit",
        args,
        {"kind": "tool_call", "status": "running"},
        repo_root=tmp_path,
        snapshots=snapshots,
    )
    _ = target.write_text("", encoding="utf-8")
    with pytest.raises(ReadScopeViolation) as caught:
        enforce_product_file_edit_guard(
            "edit",
            args,
            {"kind": "tool_call", "status": "completed"},
            repo_root=tmp_path,
            snapshots=snapshots,
        )
    assert caught.value.rule == "product_edit_destructive"
    assert target.read_text(encoding="utf-8") == prior


def test_migration_revision_edit_guarded(tmp_path: Path) -> None:
    """Деструктивный edit на ревизии Alembic откатывается так же, как на src/."""
    (tmp_path / "opsx").mkdir()
    mig_rel = "migrations/versions/0017_example.py"
    target = tmp_path / mig_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    prior = "revision = '0017_example'\n" + ("x\n" * 500)
    _ = target.write_text(prior, encoding="utf-8")
    snapshots: dict[str, str] = {}
    args = {"path": mig_rel}
    enforce_product_file_edit_guard(
        "edit",
        args,
        {"kind": "tool_call", "status": "running"},
        repo_root=tmp_path,
        snapshots=snapshots,
    )
    _ = target.write_text("", encoding="utf-8")
    with pytest.raises(ReadScopeViolation) as caught:
        enforce_product_file_edit_guard(
            "edit",
            args,
            {"kind": "tool_call", "status": "completed"},
            repo_root=tmp_path,
            snapshots=snapshots,
        )
    assert caught.value.rule == "product_edit_destructive"
    assert target.read_text(encoding="utf-8") == prior


def test_small_file_shrink_allowed(tmp_path: Path) -> None:
    """Маленькие файлы не под fail-closed (нет ложных срабатываний)."""
    (tmp_path / "opsx").mkdir()
    target = tmp_path / "src/stroy/tiny.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    prior = "x = 1\n"
    _ = target.write_text(prior, encoding="utf-8")
    snapshots: dict[str, str] = {}
    args = {"path": "src/stroy/tiny.py"}
    enforce_product_file_edit_guard(
        "edit",
        args,
        {"kind": "tool_call", "status": "running"},
        repo_root=tmp_path,
        snapshots=snapshots,
    )
    _ = target.write_text("", encoding="utf-8")
    enforce_product_file_edit_guard(
        "edit",
        args,
        {"kind": "tool_call", "status": "completed"},
        repo_root=tmp_path,
        snapshots=snapshots,
    )
    assert target.read_text(encoding="utf-8") == ""