"""Тесты единого product surface для repair hops."""

from __future__ import annotations

from pathlib import Path

from agents.config import check_paths_in_scope, resolve_read_scope
from agents.repair.embedded_authority import embed_repair_worker_authority
from agents.repair.product_surface import (
    REPAIR_PRODUCT_READ_GLOBS,
    REPAIR_PRODUCT_WRITE_GLOBS,
    format_repair_product_surface_prompt,
    repair_product_read_globs,
    repair_product_write_globs,
)


def test_product_surface_globs_stable() -> None:
    assert "migrations/**" in repair_product_read_globs()
    assert "migrations/versions/**" in repair_product_write_globs()
    assert "src/**" in REPAIR_PRODUCT_READ_GLOBS
    assert "app/**" in REPAIR_PRODUCT_READ_GLOBS
    assert "app/**" in REPAIR_PRODUCT_WRITE_GLOBS
    assert "alembic.ini" not in REPAIR_PRODUCT_WRITE_GLOBS


def test_executor_migration_read_write_split(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    scope = resolve_read_scope("IMPLEMENTATION_EXECUTOR")
    rev = "migrations/versions/rev.py"
    assert not check_paths_in_scope(
        scope, tool_name="read", paths=[rev], repo_root=repo
    ).violations
    assert not check_paths_in_scope(
        scope, tool_name="edit", paths=[rev], repo_root=repo
    ).violations
    assert check_paths_in_scope(
        scope,
        tool_name="edit",
        paths=["migrations/env.py"],
        repo_root=repo,
    ).violations


def test_embedded_prompt_lists_conventional_product_trees() -> None:
    text = format_repair_product_surface_prompt()
    assert "`src/**`" in text
    assert "`app/**`" in text
    assert "no top-level `app/`" not in text
    assert "migrations/versions" in text
    block = embed_repair_worker_authority(
        "IMPLEMENTATION_EXECUTOR",
        repository_path=str(Path(".")),
    )
    assert "REPAIR PRODUCT SURFACE" in block
    for glob in REPAIR_PRODUCT_READ_GLOBS:
        assert glob in block or glob in text