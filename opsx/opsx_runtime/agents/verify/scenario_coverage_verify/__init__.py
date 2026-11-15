"""Публичные схемы ScenarioCoverageVerify без загрузки живого агента.

Offline evidence/replay тесты читают схемы; сам агент требует SDK лишь
при явном импорте класса ScenarioCoverageVerifyAgent.
"""
from __future__ import annotations

from .schemas import (
    ScenarioCoverageGap,
    ScenarioCoverageVerifyAnswer,
    ScenarioCoverageVerifyModelAnswer,
    bind_scenario_coverage_authority,
    model_answer_from_durable,
)

__all__ = [
    "ScenarioCoverageGap",
    "ScenarioCoverageVerifyAgent",
    "ScenarioCoverageVerifyAnswer",
    "ScenarioCoverageVerifyModelAnswer",
    "bind_scenario_coverage_authority",
    "model_answer_from_durable",
]


def __getattr__(name: str):
    # API импорта класса сохранён; import схем не загружает live adapter stream.
    if name == "ScenarioCoverageVerifyAgent":
        from .agent import ScenarioCoverageVerifyAgent
        return ScenarioCoverageVerifyAgent
    if name in {"ScenarioCoverageVerifyModelAnswer", "bind_scenario_coverage_authority"}:
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
