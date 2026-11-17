"""Единый источник product surface для Bug Team repair hops (read/write).

Все restrict_allow для IMPLEMENTATION_EXECUTOR / RED_TEST_WORKER и тексты
инструкций в промпте должны ссылаться сюда, не дублировать globs.
"""

from __future__ import annotations

from typing import Final

# Роли repair-цепочки с узким product-menu (не глобальный потолок).
REPAIR_PRODUCT_HOP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "IMPLEMENTATION_EXECUTOR",
        "RED_TEST_WORKER",
    }
)

# Read: продукт, тесты, дерево миграций. ``alembic.ini`` — placeholder URL (без секретов в репо).
# ``migrations/env.py`` — URL из env; секреты в ``.env`` (глобальный deny).
REPAIR_PRODUCT_READ_GLOBS: Final[tuple[str, ...]] = (
    "src/**",
    "app/**",
    "tests/**",
    "migrations/**",
    "alembic.ini",
    "pyproject.toml",
)

# Write: только код продукта, тесты и файлы ревизий (не env.py / alembic.ini).
REPAIR_PRODUCT_WRITE_GLOBS: Final[tuple[str, ...]] = (
    "src/**",
    "app/**",
    "tests/**",
    "migrations/versions/**",
)


def is_repair_product_hop(agent_key: str | None) -> bool:
    """True для fix/red hop с узким product surface."""
    return str(agent_key or "").strip() in REPAIR_PRODUCT_HOP_KEYS


def repair_product_read_globs() -> tuple[str, ...]:
    """Repo-relative globs для read/shell/run_pytest path checks."""
    return REPAIR_PRODUCT_READ_GLOBS


def repair_product_write_globs() -> tuple[str, ...]:
    """Repo-relative globs только для write/edit."""
    return REPAIR_PRODUCT_WRITE_GLOBS


def format_repair_product_surface_prompt() -> str:
    """Краткий authoritative блок для SDK prompt (перебивает устаревший ``app/`` в worker md)."""
    read_lines = "\n".join(f"- `{p}`" for p in REPAIR_PRODUCT_READ_GLOBS)
    write_lines = "\n".join(f"- `{p}`" for p in REPAIR_PRODUCT_WRITE_GLOBS)
    return (
        "### REPAIR PRODUCT SURFACE (authoritative — overrides legacy worker WRITE_BOUNDARY)\n\n"
        "Product code lives under `src/**` or `app/**` (both are in READ and WRITE ALLOW). "
        "Use the tree this repository actually contains; do not invent a third layout.\n"
        "Alembic: `migrations/versions/**` for revision files; read `migrations/**` and "
        "`alembic.ini` for context (no secrets in repo; live DSN only via environment).\n\n"
        "READ ALLOW (repo-relative):\n"
        f"{read_lines}\n\n"
        "WRITE ALLOW (edit/write tools only):\n"
        f"{write_lines}\n"
        "`migrations/env.py` and `alembic.ini` are read-only for agents.\n"
    )