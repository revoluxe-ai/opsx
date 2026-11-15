"""Типизированные ошибки hop без привязки к провайдеру адаптера."""

from __future__ import annotations


class ToolCapabilityViolation(RuntimeError):
    """Нарушение capability manifest: не resume и не transport failure."""

    def __init__(self, code: str, *, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


class ProviderTransportFailure(RuntimeError):
    """Сетевой или bridge сбой провайдера."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class AgentContractFailure(RuntimeError):
    """Ответ агента не соответствует контракту hop."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class MachineGateFailure(RuntimeError):
    """Machine validator не прошёл после hop."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
