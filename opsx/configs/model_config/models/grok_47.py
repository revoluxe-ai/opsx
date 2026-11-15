"""Варианты параметров ``grok-4.7`` (см. ``MODELS.md``)."""

from __future__ import annotations

from enum import StrEnum

from .base_model_setting import BaseModelSetting


class Context(StrEnum):
    CTX_256K = "256k"
    CTX_500K = "500k"


class ReasoningEffort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class Grok47(BaseModelSetting):
    """Профиль ``grok-4.7`` с ``context``, ``reasoning_effort``, ``fast``."""

    name: str = "grok-4.7"
    context: Context
    reasoning_effort: ReasoningEffort
    reasoning: str | None = None
    fast: bool = False


class Grok47Low(Grok47):
    """Профиль Grok 4.7 Low: preset-slug ``grok-4.7-low``, SDK id ``grok-4.7``."""

    name: str = "grok-4.7"
