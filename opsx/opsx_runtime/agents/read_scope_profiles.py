"""Профили restrict_allow по ролям (слой ABCAgent, не транспорт).

Потолок — ``GLOBAL_READ_SCOPE.restrict_allow``; slice apply получает
узкий меню без legacy ``.cursor/pipeline``, ``opsx/opsx_runtime`` и ``opsx_tools``.
Bug Team product hops — ``agents.repair.product_surface`` (единый источник).
"""

from __future__ import annotations

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