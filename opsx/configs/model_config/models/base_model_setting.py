"""Базовый профиль настроек модели (resolve → Cursor SDK ``params``)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BaseModelSetting(BaseModel):
    """Общая форма профиля: у каждой модели заполнены только нужные поля."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    reasoning: str | None = None
    context: str | None = None
    fast: bool = False
