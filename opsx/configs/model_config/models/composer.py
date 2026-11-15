"""Профиль Composer 2.5 (slug и параметры; значения сверять с ``MODELS.md``)."""

from __future__ import annotations

from .base_model_setting import BaseModelSetting


class Composer(BaseModelSetting):
    """Настройки ``composer-2.5`` (repair hops)."""

    name: str = "composer-2.5"
    reasoning: str | None = None
    context: str | None = None
    fast: bool = False
