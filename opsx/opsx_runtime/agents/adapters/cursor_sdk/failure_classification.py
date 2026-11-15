"""Provider-specific exception recognition. Workflow stop policy stays in the caller."""
from __future__ import annotations
_STREAM_DROP_TYPE_NAMES = frozenset({"NetworkError", "RemoteProtocolError"})
_STREAM_DROP_MESSAGE_MARKERS = ("remoteprotocolerror", "incomplete chunked", "peer closed connection", "bridge request failed")

def is_cursor_provider_access_denied(exc: BaseException) -> bool:
    """True для неисправимого отказа Cursor SDK (регион / permission_denied)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == "PermissionDeniedError":
            return True
        if "permission_denied" in str(current).lower():
            return True
        current = current.__cause__ or current.__context__
    return False

def is_cursor_bridge_launch_failure(exc: BaseException) -> bool:
    """True, если локальный Cursor bridge не поднялся (discovery timeout и т.п.)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == "CursorSDKError":
            low = str(current).lower()
            if "bridge" in low or "discovery" in low:
                return True
        current = current.__cause__ or current.__context__
    return False

def is_cursor_bridge_stream_drop(exc: BaseException) -> bool:
    """True, если исключение похоже на обрыв Cursor bridge / HTTP stream.

    Учитывает ``provider_errors.NetworkError``, ``httpx`` / ``httpcore``
    ``RemoteProtocolError`` (в т.ч. в ``__cause__``), и типичные тексты
    incomplete chunked read.
    """
    try:
        from agents.adapters.provider_errors import NetworkError as CursorNetworkError
    except ImportError:
        CursorNetworkError = ()  # type: ignore[misc, assignment]

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if CursorNetworkError and isinstance(current, CursorNetworkError):
            return True
        if type(current).__name__ in _STREAM_DROP_TYPE_NAMES:
            return True
        lowered = str(current).lower()
        if any(marker in lowered for marker in _STREAM_DROP_MESSAGE_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False

def is_cursor_resume_target_gone(exc: BaseException) -> bool:
    """True, если тот же Cursor agent/run на bridge уже недоступен для resume.

    Распознаёт ``AgentNotFoundError``, ``NotFoundError`` и ``BadRequestError``
    с ``invalid_argument`` / текстом «not found» именно для run или agent.
    Прочие ``BadRequestError`` (модель, tools и т.п.) не считаются «run gone».
    """
    try:
        from agents.adapters.provider_errors import (
            AgentNotFoundError,
            BadRequestError,
            NotFoundError,
        )
    except ImportError:
        return False

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, AgentNotFoundError):
            return True
        if isinstance(current, (BadRequestError, NotFoundError)):
            lowered = str(current).lower()
            if "not found" in lowered and (
                "run" in lowered or "agent" in lowered
            ):
                return True
        current = current.__cause__ or current.__context__
    return False

def classify_failure(*, exc: BaseException) -> dict:
    if is_cursor_provider_access_denied(exc):
        return {"category": "access_denied", "code": "CURSOR_PROVIDER_UNAVAILABLE", "message": "Provider access denied", "next_action": "Restore provider access, then resume"}
    if is_cursor_bridge_launch_failure(exc):
        return {"category": "bridge_unavailable", "code": "CURSOR_BRIDGE_UNAVAILABLE", "message": "Provider bridge unavailable", "next_action": "Restore the provider bridge, then resume"}
    if is_cursor_bridge_stream_drop(exc):
        return {"category": "stream_dropped", "code": "CURSOR_BRIDGE_UNAVAILABLE", "message": "Provider stream connection lost", "next_action": "Restore the provider connection, then reconcile"}
    if is_cursor_resume_target_gone(exc):
        return {"category": "target_gone", "code": "ADAPTER_RUN_NOT_FOUND", "message": "Provider run not found", "next_action": "Reconcile the original run"}
    return {}
