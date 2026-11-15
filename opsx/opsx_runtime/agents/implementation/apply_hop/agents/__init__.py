"""Публичные классы apply-hop; реализация пока в ``agents.implementation.apply``.

Colocated DI: namespace ``apply_hop/agents/`` задаёт границу группы,
``ApplyAgent`` re-export из существующего модуля до полного переноса.
"""

from __future__ import annotations

from agents.implementation.apply.agent import ApplyAgent

__all__ = [
    "ApplyAgent",
]
