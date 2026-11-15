"""Устаревший colocated DI apply-hop; делегирует в ``CommonAgentsContainer``.

Сохранён для обратной совместимости импорта ``ApplyHopContainer``.
Новый wiring — ``agents.common.container.CommonAgentsContainer``.
"""

from __future__ import annotations

from agents.common.container import CommonAgentsContainer

ApplyHopContainer = CommonAgentsContainer

__all__ = [
    "ApplyHopContainer",
]
