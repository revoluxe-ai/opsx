"""Варианты параметров ``claude-sonnet-5-5`` (см. ``MODELS.md``)."""

from __future__ import annotations

from enum import StrEnum

from .base_model_setting import BaseModelSetting


class Context(StrEnum):
    CTX_300K = "300k"
    CTX_1M = "1m"


class Reasoning(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ClaudeSonnet55(BaseModelSetting):
    """Профиль ``claude-sonnet-5-5`` с выбранными ``context`` / ``reasoning_effort``."""

    name: str = "claude-sonnet-5-5"
    context: Context
    reasoning: Reasoning | None = None
    fast: bool = False
