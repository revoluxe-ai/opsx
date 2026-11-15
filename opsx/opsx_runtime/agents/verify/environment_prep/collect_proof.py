"""Детерминированный collect-only proof после environment_prep."""

from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path

from agents.verify.environment_prep.schemas import EnvironmentPrepCollectProof
from agents.verify.pytest_run_health import DEFAULT_GATE_TIMEOUT_SECONDS, run_collect_only
from workflows.repo_paths import resolve_opsx_repository_root


def run_environment_prep_collect_proof(
    repository_path: str,
    *,
    timeout_seconds: float = DEFAULT_GATE_TIMEOUT_SECONDS,
) -> EnvironmentPrepCollectProof:
    """``pytest --collect-only`` на ``tests/`` (как product gate scope)."""
    repo_root = resolve_opsx_repository_root(Path(repository_path))
    tests_dir = resource_path(repo_root, "tests")
    modules = ["tests"] if tests_dir.is_dir() else []
    collect = run_collect_only(repo_root, modules, timeout_seconds=timeout_seconds)
    return EnvironmentPrepCollectProof(
        modules=modules,
        exit_code=collect.exit_code,
        collect_ok=collect.ok,
    )
