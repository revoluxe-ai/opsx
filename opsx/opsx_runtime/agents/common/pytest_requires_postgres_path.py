"""Предикат пути: tier ``requires_postgres`` без зависимости от пакета ``tests``.

Используется в OPSX runtime (workflow defer, repair) и реэкспортируется в
``tests/pytest_suite_tiers`` для единого правила auto-mark и ``-m not requires_postgres``.
"""
from __future__ import annotations

from pathlib import Path

_HERMETIC_SCENARIO_PREFIX = ("scenarios", "consultation-funnel-baseline")


def _rel_parts_under_tests(path: Path, tests_root: Path) -> tuple[str, ...] | None:
    """Repo-relative ``tests/...`` parts или ``None`` если путь вне ``tests/``."""
    try:
        rel = path.resolve().relative_to(tests_root.resolve())
    except ValueError:
        return None
    return rel.parts


def path_requires_postgres(rel_parts: tuple[str, ...]) -> bool:
    """Нужен ли disposable PostgreSQL для тестов под этим путём в ``tests/``."""
    if not rel_parts:
        return False
    if rel_parts[0] == "integration":
        return True
    if rel_parts[0] == "scenarios":
        if len(rel_parts) >= 2 and rel_parts[1] == _HERMETIC_SCENARIO_PREFIX[1]:
            return False
        if len(rel_parts) >= 2:
            return True
    return False


def is_requires_postgres_path(path: Path, *, tests_root: Path) -> bool:
    """По абсолютному или относительному пути к файлу теста под ``tests_root``."""
    parts = _rel_parts_under_tests(path, tests_root)
    if parts is None:
        return False
    return path_requires_postgres(parts)


def hermetic_scenario_prefix() -> tuple[str, str]:
    """Кортеж ``(scenarios, <hermetic-change-dir>)`` для исключения из tier PG."""
    return _HERMETIC_SCENARIO_PREFIX[0], _HERMETIC_SCENARIO_PREFIX[1]
