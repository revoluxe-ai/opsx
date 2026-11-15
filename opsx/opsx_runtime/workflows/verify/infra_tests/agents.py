"""Четыре роли infra-стадии. На зелёном пути они не вызываются.

Без внедрённой функции модель не подменяется успехом: узел поднимает InfraTestsStageError.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from workflows.stop_errors import InfraTestsStageError


class _Role:
    """Общая граница: детерминированный слой передаёт callable только на UNKNOWN."""

    def __init__(self, name: str, handler: Callable[..., dict[str, Any]] | None) -> None:
        self.name = name
        self._handler = handler

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Вернуть структурированный ответ или поднять ошибку стадии."""
        if self._handler is None:
            raise InfraTestsStageError(
                f"{self.name} has no handler for an unknown failure",
                code="INFRA_AGENT_REQUIRED",
                evidence=str(payload),
                owner=self.name,
                next_action="inject a diagnose/triage/repair/runbook callable or add a signature",
            )
        return self._handler(payload)


class InfraEnvDoctor(_Role):
    """Неизвестная сигнатура среды."""

    def __init__(self, handler: Callable[..., dict[str, Any]] | None = None) -> None:
        super().__init__("InfraEnvDoctor", handler)


class InfraTestTriage(_Role):
    """Падение без табличной сигнатуры."""

    def __init__(self, handler: Callable[..., dict[str, Any]] | None = None) -> None:
        super().__init__("InfraTestTriage", handler)


class InfraHarnessRepair(_Role):
    """Ремонт harness. Не ослабляет assertions и не снимает маркеры."""

    def __init__(self, handler: Callable[..., dict[str, Any]] | None = None) -> None:
        super().__init__("InfraHarnessRepair", handler)


class InfraRunbookKeeper(_Role):
    """Формулирует proposed-запись. verified ставит только store по доказательству."""

    def __init__(self, handler: Callable[..., dict[str, Any]] | None = None) -> None:
        super().__init__("InfraRunbookKeeper", handler)
