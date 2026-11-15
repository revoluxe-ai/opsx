"""Компиляция пресетов production pipeline (PP) под версию Bugbot-агента."""

from __future__ import annotations

from dataclasses import dataclass

from .agent_roles import AgentRole
from .models.base_model_setting import BaseModelSetting

# Базовые verify-агенты: ``BugBotAgent`` → ``BUG_BOT``;
# ``ImplVerifyAgent`` → ``VERIFY_IMPL`` (``/opsx-verify mode=impl``).


@dataclass(frozen=True, slots=True)
class BugBotVersion:
    """Прокси базового Bugbot-агента (wire-роль ``BUG_BOT``)."""

    role: AgentRole = AgentRole.BUG_BOT


DEFAULT_BUGBOT_VERSION = BugBotVersion()


@dataclass(frozen=True, slots=True)
class PpCompiledModels:
    """Собранные профили моделей PP: repair hops + лестница search-rounds."""

    super_low: BaseModelSetting
    search_round_low: BaseModelSetting
    search_round_medium: BaseModelSetting
    search_round_high: BaseModelSetting


def compile_pp(bugbot_version: BugBotVersion = DEFAULT_BUGBOT_VERSION) -> PpCompiledModels:
    """Собрать PP-пресеты для базового Bugbot (``AgentRole.BUG_BOT``)."""
    if bugbot_version.role is not AgentRole.BUG_BOT:
        raise ValueError(
            f"expected base bugbot agent {AgentRole.BUG_BOT!s}, got {bugbot_version.role!s}",
        )
    return _compile_default_pp(bugbot_version)


def _compile_default_pp(bugbot_version: BugBotVersion) -> PpCompiledModels:
    _ = bugbot_version
    from .model_presets import (
        DEFAULT_HOP_MODEL,
        grok_high,
        grok_medium,
        super_low_model,
    )

    return PpCompiledModels(
        super_low=super_low_model,
        search_round_low=DEFAULT_HOP_MODEL,
        search_round_medium=grok_medium,
        search_round_high=grok_high,
    )
