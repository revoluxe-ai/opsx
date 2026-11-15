"""Варианты параметров ``grok-4.6`` (см. ``MODELS.md``)."""

from __future__ import annotations

from enum import StrEnum

from .base_model_setting import BaseModelSetting


class ReasoningEffort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"


class Grok46(BaseModelSetting):
    """Профиль ``grok-4.6`` с ``reasoning_effort`` (SDK param ``effort``) и ``fast``."""

    name: str = "grok-4.6"
    reasoning_effort: ReasoningEffort
    fast: bool = False


class Grok46Low(Grok46):
    """Профиль Grok 4.6 Low: preset-slug ``grok-4.6-low``, SDK id ``grok-4.6``."""

    name: str = "grok-4.6"
