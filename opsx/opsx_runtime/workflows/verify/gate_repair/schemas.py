"""Контракты subprocess gates и handoff diagnostics для gate-repair."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GateCheckResult(BaseModel):
    """Результат одного subprocess gate (фикстуры smoke)."""

    model_config = ConfigDict(extra="forbid")

    check_id: Literal["pytest", "pyright", "mypy"]
    command: list[str]
    exit_code: int
    duration_ms: int
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str | None = None


class QualityGateRawBundle(BaseModel):
    """Сырые результаты трёх проверок для ``extract_gate_diagnostics``."""

    model_config = ConfigDict(extra="forbid")

    pytest: GateCheckResult
    pyright: GateCheckResult
    mypy: GateCheckResult
