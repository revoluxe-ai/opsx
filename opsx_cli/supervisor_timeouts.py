"""Преобразование и валидация таймаутов супервизора (минуты → секунды)."""

from __future__ import annotations

from dataclasses import dataclass

from .context import OpsxError

DEFAULT_WORKFLOW_TIMEOUT_MINUTES = 45.0
DEFAULT_SUPERVISOR_TIMEOUT_MINUTES = 180.0
DEFAULT_MAX_ATTEMPTS = 5
MIN_FIX_BUDGET_SECONDS = 300.0
DEFAULT_SILENCE_LIMIT_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class SupervisorTimeouts:
    """Согласованные лимиты wall-clock для одного supervised run."""

    workflow_timeout_seconds: float
    supervisor_timeout_seconds: float
    max_attempts: int
    silence_limit_seconds: float = DEFAULT_SILENCE_LIMIT_SECONDS
    min_fix_budget_seconds: float = MIN_FIX_BUDGET_SECONDS

    @classmethod
    def from_minutes(
        cls,
        *,
        workflow_timeout_minutes: float,
        supervisor_timeout_minutes: float,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> SupervisorTimeouts:
        """Собрать лимиты из минут; fail-closed при неверной конфигурации."""
        if workflow_timeout_minutes <= 0 or supervisor_timeout_minutes <= 0:
            raise OpsxError(
                "TIMEOUT_CONFIG_INVALID",
                "workflow_timeout_minutes и supervisor_timeout_minutes должны быть > 0.",
            )
        if supervisor_timeout_minutes < workflow_timeout_minutes:
            raise OpsxError(
                "TIMEOUT_CONFIG_INVALID",
                "supervisor_timeout_minutes должен быть >= workflow_timeout_minutes.",
            )
        if max_attempts < 1:
            raise OpsxError("TIMEOUT_CONFIG_INVALID", "max_attempts должен быть >= 1.")
        return cls(
            workflow_timeout_seconds=workflow_timeout_minutes * 60.0,
            supervisor_timeout_seconds=supervisor_timeout_minutes * 60.0,
            max_attempts=max_attempts,
        )