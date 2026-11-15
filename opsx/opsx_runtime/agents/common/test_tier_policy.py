"""Единая политика tier тестов: active (hermetic) vs infra (PostgreSQL).

Канонические выражения pytest ``-m`` и классификация пути/item живут только здесь.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import pytest

ACTIVE_EXPR = "not requires_postgres"
INFRA_EXPR = "requires_postgres"

TierName = Literal["active", "infra"]


@dataclass(frozen=True, slots=True)
class TierDecision:
    """Решение по tier: уровень, ресурсы и краткие причины."""

    tier: TierName
    resources: tuple[str, ...]
    reasons: tuple[str, ...]


def _rel_parts_under_tests(path: Path, tests_root: Path) -> tuple[str, ...] | None:
    try:
        rel = path.resolve().relative_to(tests_root.resolve())
    except ValueError:
        return None
    return rel.parts


def classify_path_parts(rel_parts: tuple[str, ...]) -> TierDecision:
    """Классифицировать repo-relative части пути под ``tests/``."""
    if not rel_parts:
        return TierDecision("active", (), ("empty_path",))
    if rel_parts[0] == "integration":
        return TierDecision("infra", ("postgres",), ("path:integration"))
    if rel_parts[0] == "scenarios" and len(rel_parts) >= 2:
        return TierDecision("infra", ("postgres",), ("path:scenarios"))
    if rel_parts[0] == "e2e":
        return TierDecision("infra", ("postgres",), ("path:e2e"))
    return TierDecision("active", (), ("default_active",))


def classify_path(path: Path, *, tests_root: Path) -> TierDecision:
    """Классифицировать файл теста по абсолютному пути."""
    parts = _rel_parts_under_tests(path, tests_root)
    if parts is None:
        return TierDecision("active", (), ("outside_tests",))
    return classify_path_parts(parts)


def path_requires_postgres(rel_parts: tuple[str, ...]) -> bool:
    """True, если путь относится к infra-tier (нужен disposable PostgreSQL)."""
    return classify_path_parts(rel_parts).tier == "infra"


def is_requires_postgres_path(path: Path, *, tests_root: Path) -> bool:
    """Удобная обёртка для repair/runtime: нужен ли PG по пути файла."""
    return classify_path(path, tests_root=tests_root).tier == "infra"


def item_requires_postgres(item: pytest.Item) -> bool:
    """Нужен ли маркер ``requires_postgres`` для собранного pytest item."""
    if item.get_closest_marker("requires_postgres") is not None:
        return True
    from opsx.project_context import project_root
    tests_root = project_root() / "tests"
    if not tests_root.is_dir():
        tests_root = Path.cwd() / "tests"
    decision = classify_path(Path(str(item.path)), tests_root=tests_root)
    if decision.tier == "infra":
        return True
    if item.get_closest_marker("integration") is not None or item.get_closest_marker("e2e") is not None:
        parts = _rel_parts_under_tests(Path(str(item.path)), tests_root)
        if parts is not None:
            return classify_path_parts(parts).tier == "infra"
    return False


def workflow_active_markexpr() -> str:
    """Выражение pytest для active-gates verify (единственный литерал)."""
    return ACTIVE_EXPR


def workflow_infra_markexpr() -> str:
    """Выражение pytest для infra-only прогона."""
    return INFRA_EXPR
