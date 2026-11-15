"""Промпт ok_probe — минимальная инструкция без контекста репозитория."""

from __future__ import annotations


def get_prompt() -> str:
    """Вернуть текст SDK prompt для ``OkProbeAgent``."""
    return (
        "Ответь ОК. Верни ровно один JSON-объект и больше ничего. "
        "Не читай репозиторий и не вызывай инструменты."
    )
