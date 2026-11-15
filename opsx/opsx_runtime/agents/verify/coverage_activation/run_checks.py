"""Запуск focused static/test checks после активации."""

from __future__ import annotations

from opsx.project_context import resource_path

import shutil
from pathlib import Path
from typing import Any

from workflows.implementation.node_subprocess import run_subprocess_utf8


def _poetry_prefix(repo_root: Path) -> list[str]:
    poetry = shutil.which("poetry")
    if poetry:
        return [poetry]
    return ["python", "-m", "poetry"]


def run_mypy_on_paths(repo_root: Path, paths: list[str]) -> dict[str, Any]:
    """Focused mypy для конкретных файлов (CLI targets, ``mypy.ini`` в корне)."""
    if not paths:
        return {"ran": False, "exit_code": 0, "stdout": "", "stderr": ""}
    config = resource_path(repo_root, "mypy.ini")
    if not config.is_file():
        config = resource_path(repo_root, ".mypy.ini")
    if not config.is_file():
        return {"ran": False, "exit_code": 2, "stdout": "", "stderr": "mypy.ini missing"}
    prefix = _poetry_prefix(repo_root)
    cmd = [
        *prefix,
        "run",
        "python",
        "-m",
        "mypy",
        "--config-file",
        str(config),
        *[str(resource_path(repo_root, p)) for p in paths],
    ]
    proc = run_subprocess_utf8(cmd, cwd=repo_root)
    return {
        "ran": True,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
        "command": " ".join(cmd),
    }


def run_pyright_on_paths(repo_root: Path, paths: list[str]) -> dict[str, Any]:
    """Focused pyright для конкретных файлов (не whole project)."""
    if not paths:
        return {"ran": False, "exit_code": 0, "stdout": "", "stderr": ""}
    config = resource_path(repo_root, "pyrightconfig_strict.json")
    if not config.is_file():
        return {"ran": False, "exit_code": 2, "stdout": "", "stderr": "config missing"}
    prefix = _poetry_prefix(repo_root)
    cmd = [
        *prefix,
        "run",
        "python",
        "-m",
        "pyright",
        "--project",
        str(config),
        *[str(resource_path(repo_root, p)) for p in paths],
    ]
    proc = run_subprocess_utf8(cmd, cwd=repo_root)
    return {
        "ran": True,
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
        "command": " ".join(cmd),
    }


def run_pytest_on_paths(repo_root: Path, test_paths: list[str]) -> dict[str, Any]:
    """Focused pytest для reactivated tests (через ``pytest_execute``)."""
    if not test_paths:
        return {"ran": False, "exit_code": 0, "stdout": "", "stderr": ""}
    from agents.repair.scenario_test_repair.workflow_pytest_defer import (
        workflow_skip_pytest_module,
    )
    from workflows.verify.pytest_execute import PytestExecuteRequest
    from workflows.verify.pytest_execute.invoke import run_pytest_execute_sync

    runnable = [path for path in test_paths if not workflow_skip_pytest_module(repo_root, path)]
    if not runnable:
        return {
            "ran": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "WORKFLOW_PYTEST_DEFERRED_SKIP",
            "command": "pytest_execute_skipped",
        }
    result = run_pytest_execute_sync(
        repo_root,
        PytestExecuteRequest(scope_kind="MODULES", module_paths=list(runnable)),
    )
    gate = result.gate_check
    exit_code = gate.exit_code if gate is not None else result.collect_proof.collect_exit_code
    log = result.run_log or (gate.stderr_tail if gate else result.collect_proof.collect_tail)
    return {
        "ran": True,
        "exit_code": exit_code,
        "stdout": log[-4000:],
        "stderr": "",
        "command": "pytest_execute",
    }


def checker_available(repo_root: Path) -> dict[str, bool]:
    """Какие checkers доступны в окружении."""
    pyright_cfg = (resource_path(repo_root, "pyrightconfig_strict.json")).is_file()
    mypy_cfg = (resource_path(repo_root, "mypy.ini")).is_file() or (resource_path(repo_root, ".mypy.ini")).is_file()
    poetry = shutil.which("poetry") is not None
    return {
        "pyright_config": pyright_cfg,
        "mypy_config": mypy_cfg,
        "poetry": poetry,
        "pytest": poetry,
    }
