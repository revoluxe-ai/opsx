"""Динамические label-множества из invest_yalta исходников @ BASE_SHA (read-only)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from tools.openspec.consultation_baseline._parse_literals import literal_from_module
from tools.openspec.consultation_baseline._source_python import load_python_source

FSM_GIT_PATH = "src/stroy/channels/telegram/invest_yalta/fsm.py"
GOALS_GIT_PATH = "src/stroy/channels/telegram/invest_yalta/goals.py"
PRESENT_GIT_PATH = "src/stroy/channels/telegram/invest_yalta/telegram_present.py"


@dataclass
class RuntimeDynamics:
    """Предикаты для веток ``_handle_cta``, зависящих от runtime constants."""

    is_goal_option_label: Callable[[str], bool]
    experience_presets: frozenset[str]
    platform_labels: frozenset[str]
    off_path_buttons: frozenset[str]
    budget_skip_labels: frozenset[str]
    currency_suffix_labels: frozenset[str]
    tz_preset_matches: Callable[[str], bool]
    active_booking_cta_labels: frozenset[str]


def _goal_labels_from_goals_source(goals_source: str) -> frozenset[str]:
    catalog = literal_from_module(goals_source, "GOAL_CATALOG")
    labels: set[str] = set()
    for item in catalog:
        if not isinstance(item, tuple) or len(item) != 2:
            continue
        labels.add(str(item[1]))
    if not labels:
        raise ValueError("GOAL_CATALOG labels not found in goals.py")
    return frozenset(labels)


def _tz_city_names(fsm_source: str) -> frozenset[str]:
    tz_block = literal_from_module(fsm_source, "_TZ_PRESETS")
    if not isinstance(tz_block, dict):
        raise ValueError("_TZ_PRESETS must be dict literal")
    return frozenset(str(k) for k in tz_block.keys())


def load_runtime_dynamics(repo_root: Path, base_sha: str) -> RuntimeDynamics:
    """Загрузить константы для классификации из git pin (без import ``stroy``)."""

    fsm_source = load_python_source(repo_root, base_sha, FSM_GIT_PATH)
    goals_source = load_python_source(repo_root, base_sha, GOALS_GIT_PATH)
    present_source = load_python_source(repo_root, base_sha, PRESENT_GIT_PATH)

    experience_presets = frozenset(literal_from_module(fsm_source, "_EXPERIENCE_PRESETS"))
    budget_skip_labels = frozenset(literal_from_module(fsm_source, "_BUDGET_SKIP_LABELS"))
    currency_suffix = literal_from_module(fsm_source, "_CURRENCY_SUFFIX")
    currency_suffix_labels = frozenset(str(k) for k in currency_suffix.keys())
    off_path_buttons = frozenset(literal_from_module(present_source, "OFF_PATH_BUTTONS"))
    goal_labels = _goal_labels_from_goals_source(goals_source)
    tz_cities = _tz_city_names(fsm_source)

    def _tz_matches(label: str) -> bool:
        city = label.removeprefix("✅ ").strip()
        if city in tz_cities:
            return True
        return any(city.startswith(f"{name} (GMT") for name in tz_cities)

    def _is_goal(label: str) -> bool:
        return label in goal_labels

    return RuntimeDynamics(
        is_goal_option_label=_is_goal,
        experience_presets=experience_presets,
        platform_labels=frozenset(),
        off_path_buttons=off_path_buttons,
        budget_skip_labels=budget_skip_labels,
        currency_suffix_labels=currency_suffix_labels,
        tz_preset_matches=_tz_matches,
        active_booking_cta_labels=frozenset({"Посмотреть вариант", "Проверить ответ"}),
    )
