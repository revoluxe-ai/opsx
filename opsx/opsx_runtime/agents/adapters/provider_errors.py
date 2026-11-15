"""Neutral provider error types for agents/workflows (no provider package import)."""


class AgentNotFoundError(Exception):
    """Adapter agent id not found at provider."""


class APITimeoutError(Exception):
    """Provider API timeout."""


class BadRequestError(Exception):
    """Malformed provider request."""


class InternalServerError(Exception):
    """Provider internal error."""


class NetworkError(Exception):
    """Provider network failure."""


class NotFoundError(Exception):
    """Provider resource not found."""


__all__ = [
    "AgentNotFoundError",
    "APITimeoutError",
    "BadRequestError",
    "InternalServerError",
    "NetworkError",
    "NotFoundError",
]
