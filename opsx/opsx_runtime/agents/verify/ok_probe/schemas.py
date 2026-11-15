"""Контракт JSON-ответа probe-агента ok_probe."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class OkProbeAnswer(BaseModel):
    """Единственное поле — строка ``OK``."""

    model_config = ConfigDict(extra="forbid")

    answer: Literal["OK"]
