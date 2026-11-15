"""Единая сборка argv product pytest для OPSX workflow (gates, collect, adoption).

Импортируется из verify runtime (``sys.path`` = ``opsx/opsx_runtime``), не из пакета
``tests``. Значение markexpr синхронизировать с ``tests/pytest_suite_tiers.WORKFLOW_PYTEST_MARKEXPR``.
"""

from __future__ import annotations

from pathlib import Path

from agents.common.test_tier_policy import workflow_active_markexpr
from workflows.verify.gate_repair.pytest_gate_timeout import QUALITY_GATE_PER_TEST_SECONDS

WORKFLOW_PYTEST_MARKEXPR = workflow_active_markexpr()

PRODUCT_TESTS_DIR = "tests"

# Совпадает с QUALITY_GATE_PER_TEST_SECONDS: плагин pytest-timeout на каждый тест.
WORKFLOW_PYTEST_TIMEOUT_SECONDS = int(QUALITY_GATE_PER_TEST_SECONDS)
WORKFLOW_PYTEST_TIMEOUT_METHOD = "thread"


def workflow_pytest_timeout_flags(
    timeout_seconds: int = WORKFLOW_PYTEST_TIMEOUT_SECONDS,
) -> list[str]:
    """Флаги pytest-timeout. На Windows метод ``thread``: ``signal`` тест не режет."""
    return [
        "--timeout",
        str(timeout_seconds),
        "--timeout-method",
        WORKFLOW_PYTEST_TIMEOUT_METHOD,
    ]


def apply_workflow_pytest_timeout(argv: list[str], timeout_seconds: int) -> list[str]:
    """Подменить ``--timeout`` в уже собранном argv (пробный прогон одного node)."""
    updated = list(argv)
    if "--timeout" in updated:
        index = updated.index("--timeout")
        updated[index + 1] = str(timeout_seconds)
        return updated
    return [*updated, *workflow_pytest_timeout_flags(timeout_seconds)]


def workflow_pytest_tail_flags(
    repo_root: Path,
    *,
    include_run_report: bool = False,
    quiet: bool = True,
) -> list[str]:
    """Хвост argv: ignore opsx, workflow markexpr, per-test timeout, verbosity, short tb."""
    tests_dir = repo_root.resolve() / PRODUCT_TESTS_DIR
    verbosity = "-q" if quiet else "-v"
    flags = [
        "--ignore=" + str(tests_dir / "opsx"),
        "-m",
        WORKFLOW_PYTEST_MARKEXPR,
        verbosity,
        "--tb=short",
        "-p",
        "workflows.verify.gate_repair.pytest_opsx_gate_progress",
        *workflow_pytest_timeout_flags(),
    ]
    if include_run_report:
        flags.extend(["-r", "fE"])
    return flags


def default_workflow_pytest_paths(repo_root: Path) -> list[str]:
    """Каталог ``tests/`` для full-suite workflow gates."""
    tests_dir = repo_root.resolve() / PRODUCT_TESTS_DIR
    return [str(tests_dir)] if tests_dir.is_dir() else []


def build_workflow_pytest_argv(
    repo_root: Path,
    *,
    paths: list[str],
    poetry_prefix: list[str] | None = None,
    python_executable: str | None = None,
    collect_only: bool = False,
    quiet: bool | None = None,
) -> list[str]:
    """Собрать полный argv subprocess для workflow product pytest."""
    repo_root = repo_root.resolve()
    resolved_quiet = True if quiet is None else quiet
    tail = workflow_pytest_tail_flags(
        repo_root,
        include_run_report=not collect_only,
        quiet=resolved_quiet,
    )
    collect_flags = ["--collect-only"] if collect_only else []
    if poetry_prefix is not None:
        return [
            *poetry_prefix,
            "run",
            "python",
            "-m",
            "pytest",
            *collect_flags,
            *paths,
            *tail,
        ]
    if python_executable is not None:
        return [python_executable, "-m", "pytest", *collect_flags, *paths, *tail]
    msg = "build_workflow_pytest_argv requires poetry_prefix or python_executable"
    raise ValueError(msg)
