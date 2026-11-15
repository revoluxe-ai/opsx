"""Публичные импорты Apply без преждевременной загрузки live-агента.

Тестам нужны схемы ApplyAnswer, но не запуск модели. Прямой импорт .agent
из этого __init__ раньше подтягивал адаптер уже на этапе collection.
"""
from __future__ import annotations

from .schemas import ApplyAnswer

__all__ = ["ApplyAgent", "ApplyAnswer"]


def __getattr__(name: str):
    # Python вызывает __getattr__ только для отсутствующего атрибута модуля.
    # Старый публичный импорт ApplyAgent остаётся рабочим; только явный
    # запрос этого класса загрузит .agent и его live-зависимости.
    if name == "ApplyAgent":
        from .agent import ApplyAgent
        return ApplyAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
