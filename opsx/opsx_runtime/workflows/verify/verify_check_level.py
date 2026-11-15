"""Модели verify-check для bugbot / security: активен только level 1 (low).

Level 2–3 (medium/high) зарезервированы в ``resolve_verify_check_level_model``;
``VERIFY_CHECK_MAX_LEVEL = 1`` отключает эскалацию в subgraph.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

from agents.state import GraphState

_CONFIGS_ROOT = Path(__file__).resolve().parents[3] / "configs"
if str(_CONFIGS_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONFIGS_ROOT))

from model_config.bug_bot_model import high_model, low_model, medium_model  # noqa: E402
from model_config.models.base_model_setting import BaseModelSetting  # noqa: E402
from agents.adapters.hop_model_selection import model_selection_for_hop  # noqa: E402
from model_config.resolve_model import ModelResolveResult  # noqa: E402

VERIFY_CHECK_MAX_LEVEL = 1
"""Максимальный level bugbot/security (только low; без medium/high проходов)."""


def verify_check_level_state_key(name_prefix: str) -> str:
    """Ключ ``GraphState`` для текущего level (1..3) проверки ``name_prefix``."""
    return f"{name_prefix}_level"


def read_verify_check_level(state: Mapping[str, Any], name_prefix: str) -> int:
    """Текущий level проверки; по умолчанию 1."""
    raw = state.get(verify_check_level_state_key(name_prefix))
    if isinstance(raw, int) and 1 <= raw <= VERIFY_CHECK_MAX_LEVEL:
        return raw
    return 1


def check_workflow_uses_level_ladder(name_prefix: str) -> bool:
    """True только для bugbot и security (не project_bug и не gate_repair)."""
    return name_prefix in ("bugbot", "security")


def resolve_verify_check_level_model(level: int) -> BaseModelSetting:
    """Профиль модели для level 1/2/3 (low / medium / high)."""
    if level == 1:
        return low_model
    if level == 2:
        return medium_model
    if level == 3:
        return high_model
    msg = f"verify check level out of range: {level}"
    raise ValueError(msg)


def patch_verify_check_level_model(state: Mapping[str, Any], name_prefix: str) -> dict[str, Any]:
    """Patch ``model`` в ``GraphState`` под текущий level проверки."""
    level = read_verify_check_level(state, name_prefix)
    profile = resolve_verify_check_level_model(level)
    return {"model": model_selection_for_hop(ModelResolveResult(model=profile))}


def patch_advance_verify_check_level(state: GraphState, name_prefix: str) -> dict[str, Any]:
    """Следующий level после зелёного прохода; сбрасывает только счётчик inner loop снаружи."""
    key = verify_check_level_state_key(name_prefix)
    current = read_verify_check_level(state, name_prefix)
    if current >= VERIFY_CHECK_MAX_LEVEL:
        return {}
    return {key: current + 1}
