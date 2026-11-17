"""Профили restrict_allow по ролям (слой ABCAgent, не транспорт).

Потолок — ``GLOBAL_READ_SCOPE.restrict_allow``; slice apply получает
узкий меню без legacy ``.cursor/pipeline``, ``opsx/opsx_runtime`` и ``opsx_tools``.
Bug Team product hops — ``agents.repair.product_surface`` (единый источник).
"""

from __future__ import annotations

import json
import os

from agents.config import GLOBAL_READ_SCOPE, path_matches_pattern
from agents.repair.product_surface import (
    REPAIR_PRODUCT_HOP_KEYS,
    repair_product_read_globs,
)

_SLICE_APPLY_KEYS = frozenset(
    {
        "apply_design",
        "apply_design_review",
        "apply_scaffold",
        "apply_red",
        "apply_implement",
        "apply_green",
        "apply",
    }
)

_APPLY_SLICE_RESTRICT: tuple[str, ...] = (
    "src/**",
    "app/**",
    "tests/**",
    "openspec/changes/**",
    "openspec/decisions/**",
    "openspec/specs/**",
    "migrations/**",
    "pyproject.toml",
    "poetry.lock",
)


def _within_global_ceiling(pattern: str) -> bool:
    """Паттерн роли не шире глобального потолка (эвристика по префиксам)."""
    ceiling = GLOBAL_READ_SCOPE.restrict_allow or ()
    for global_pat in ceiling:
        if pattern == global_pat:
            return True
        if pattern.endswith("/**") and any(
            path_matches_pattern(pattern[:-3], global_pat) for global_pat in ceiling
        ):
            return True
        root = pattern.split("/")[0] if "/" in pattern else pattern
        if any(
            global_pat == pattern
            or global_pat.startswith(f"{root}/")
            or global_pat.startswith(f"{root}*")
            for global_pat in ceiling
        ):
            return True
    return False


_UNSAFE_SOURCE_ROOT_PREFIXES: tuple[str, ...] = (
    "opsx/",
    ".opsx/",
    ".cursor/",
    ".git/",
    ".github/",
    "node_modules/",
    "tools/",
    ".venv/",
)


def product_source_root_globs_from_env() -> tuple[str, ...]:
    """Глобы ``product.source_roots`` из ``OPSX_PRODUCT_SOURCE_ROOTS_JSON``.

    Пустой или отсутствующий env не подменяет деревья ``src/**`` и ``app/**``.
    Корни вне product (``opsx/``, ``.git/``, …) не расширяют restrict.
    """
    raw = os.environ.get("OPSX_PRODUCT_SOURCE_ROOTS_JSON", "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    globs: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        norm = item.replace("\\", "/").strip().strip("/")
        if not norm or ".." in norm.split("/"):
            continue
        if any(
            norm == prefix.rstrip("/") or norm.startswith(prefix)
            for prefix in _UNSAFE_SOURCE_ROOT_PREFIXES
        ):
            continue
        if norm.startswith(".env"):
            continue
        globs.append(norm if norm.endswith("/**") else f"{norm}/**")
    return tuple(dict.fromkeys(globs))


def restrict_allow_for_agent(agent_key: str | None) -> tuple[str, ...] | None:
    """Узкий allow-list роли или ``None`` (оставить глобальный потолок)."""
    key = str(agent_key or "").strip()
    if key in _SLICE_APPLY_KEYS:
        merged = tuple(
            p for p in _APPLY_SLICE_RESTRICT if _within_global_ceiling(p)
        )
        return merged or _APPLY_SLICE_RESTRICT
    if key in REPAIR_PRODUCT_HOP_KEYS:
        merged = tuple(
            p for p in repair_product_read_globs() if _within_global_ceiling(p)
        )
        return merged or repair_product_read_globs()
    return None