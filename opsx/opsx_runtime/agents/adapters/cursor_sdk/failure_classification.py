"""Provider-specific exception recognition. Workflow stop policy stays in the caller."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

_LOG = logging.getLogger(__name__)
_T = TypeVar("_T")
# Extra create/resume attempts after the SDK's own HTTP retries. The first
# call is not counted here. Backoff is long enough for a capacity blip and
# bounded so a stuck provider still becomes a typed stop.
_CAPACITY_EXTRA_ATTEMPTS = 3
_CAPACITY_BACKOFF_SECONDS = (5.0, 15.0, 30.0)
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

def is_cursor_provider_at_capacity(exc: BaseException) -> bool:
    """True when Cursor refused to start work because the server is at capacity.

    Distinct from an active-run conflict and from a dropped stream: the
    create/resume call did not start a run, so the same call can be repeated.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if "server at capacity" in str(current).lower():
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
    if is_cursor_provider_at_capacity(exc):
        return {
            "category": "provider_capacity",
            "code": "CURSOR_PROVIDER_AT_CAPACITY",
            "message": "Provider server at capacity",
            "next_action": "Retry the same hop after provider capacity recovers",
        }
    if is_cursor_resume_target_gone(exc):
        return {"category": "target_gone", "code": "ADAPTER_RUN_NOT_FOUND", "message": "Provider run not found", "next_action": "Reconcile the original run"}
    return {}


async def call_with_provider_capacity_retry(operation: Callable[[], Awaitable[_T]]) -> _T:
    """Repeat one provider call after ``server at capacity``.

    The callable must be a single create, send, or wait of a run that was
    refused or already accepted. It must not send a second prompt: a retry
    of send is only valid when that send did not return a run, and a retry
    of wait only re-reads the same run. Any other exception, including
    cancellation and a dropped stream, propagates on the first occurrence.
    When the extra attempts are spent, the original capacity error
    propagates for the caller to map to a typed stop.
    """
    extra = 0
    while True:
        try:
            return await operation()
        except BaseException as exc:
            if not is_cursor_provider_at_capacity(exc) or extra >= _CAPACITY_EXTRA_ATTEMPTS:
                raise
            delay = _CAPACITY_BACKOFF_SECONDS[extra]
            extra += 1
            _LOG.warning(
                "cursor_provider_at_capacity retry=%s/%s backoff_s=%s error=%s",
                extra,
                _CAPACITY_EXTRA_ATTEMPTS,
                delay,
                exc,
            )
            await asyncio.sleep(delay)


async def wait_with_provider_capacity_retry(
    operation: Callable[[], Awaitable[_T]],
) -> _T:
    """Re-wait an already accepted run when the provider is at capacity.

    ``operation`` must call wait on the same run object. It must not create
    an agent or send a prompt. Stream drops and contract errors are not
    capacity and are not retried here.
    """
    return await call_with_provider_capacity_retry(operation)
