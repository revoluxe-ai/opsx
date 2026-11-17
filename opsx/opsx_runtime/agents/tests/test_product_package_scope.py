"""Poetry packages вне ``src/**`` входят в scope slice/repair и не открывают opsx."""

from __future__ import annotations

from pathlib import Path

from agents.config import check_paths_in_scope, resolve_read_scope
from agents.product_package_scope import merge_declared_product_packages

_PYPROJECT = """\
[tool.poetry]
name = "sample"
version = "0.1.0"
packages = [
  { include = "pkg_billing" },
  { include = "worker", from = "services" },
  { include = "openspec" },
  { include = "opsx" },
]
"""


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "opsx").mkdir(parents=True)
    (repo / "pkg_billing").mkdir()
    (repo / "services" / "worker").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    return repo


def test_scaffold_read_allows_poetry_package_outside_src(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    scope = resolve_read_scope("apply_scaffold")
    merged = merge_declared_product_packages(
        scope,
        {"repository_path": str(repo)},
        agent_key="apply_scaffold",
    )
    billing = check_paths_in_scope(
        merged,
        tool_name="read",
        paths=["pkg_billing/mod.py"],
        repo_root=repo,
    )
    worker = check_paths_in_scope(
        merged,
        tool_name="edit",
        paths=["services/worker/run.py"],
        repo_root=repo,
    )
    assert not billing.violations
    assert not worker.violations


def test_poetry_packages_do_not_open_opsx_or_schema_trees(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    scope = resolve_read_scope("apply_scaffold")
    merged = merge_declared_product_packages(
        scope,
        {"repository_path": str(repo)},
        agent_key="apply_scaffold",
    )
    schema = check_paths_in_scope(
        merged,
        tool_name="read",
        paths=["openspec/schemas/opsx.apply_scaffold.json"],
        repo_root=repo,
    )
    runtime = check_paths_in_scope(
        merged,
        tool_name="read",
        paths=["opsx/opsx_runtime/agents/config.py"],
        repo_root=repo,
    )
    assert schema.violations
    assert schema.violations[0].rule == "outside_restrict_allow"
    assert runtime.violations


def test_unrelated_role_does_not_gain_poetry_packages(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    scope = resolve_read_scope(None)
    merged = merge_declared_product_packages(
        scope,
        {"repository_path": str(repo)},
        agent_key=None,
    )
    denied = check_paths_in_scope(
        merged,
        tool_name="read",
        paths=["pkg_billing/mod.py"],
        repo_root=repo,
    )
    assert denied.violations
    assert merged.restrict_allow == scope.restrict_allow


def test_repair_write_includes_poetry_package(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    scope = resolve_read_scope("IMPLEMENTATION_EXECUTOR")
    merged = merge_declared_product_packages(
        scope,
        {"repository_path": str(repo)},
        agent_key="IMPLEMENTATION_EXECUTOR",
    )
    edited = check_paths_in_scope(
        merged,
        tool_name="edit",
        paths=["pkg_billing/mod.py"],
        repo_root=repo,
    )
    assert not edited.violations
    assert scope.restrict_write_allow is not None
    assert merged.restrict_write_allow is not None
    assert "pkg_billing/**" in merged.restrict_write_allow
