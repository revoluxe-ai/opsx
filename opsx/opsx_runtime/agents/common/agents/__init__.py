"""Публичные hop-агенты общего слоя: apply и commit.

Colocated DI: namespace ``common/agents/`` задаёт границу группы;
классы re-export из существующих модулей (apply — implementation, commit — ``commit_agent``).
"""

from __future__ import annotations

from agents.implementation.apply.agent import ApplyAgent

from ..commit_agent import CommitAgent

__all__ = [
    "ApplyAgent",
    "CommitAgent",
]
