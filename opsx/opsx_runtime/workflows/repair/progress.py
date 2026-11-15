"""Structured progress events for repair workflow observers.

The workflow normally runs without an observer.  The local owner UI installs a
callback for the duration of one run and receives semantic stage transitions;
other callers keep the existing behaviour and pay no coordination cost.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

ProgressCallback = Callable[[dict[str, Any]], None]

_progress_callback: ContextVar[ProgressCallback | None] = ContextVar(
    "repair_progress_callback",
    default=None,
)


def emit_repair_progress(
    *,
    event: str,
    stage: str,
    state: str,
    detail: str = "",
    data: Mapping[str, Any] | None = None,
) -> None:
    """Emit one best-effort progress event to the active observer."""

    callback = _progress_callback.get()
    if callback is None:
        return
    payload: dict[str, Any] = {
        "event": event,
        "stage": stage,
        "state": state,
        "detail": detail,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if data:
        payload["data"] = dict(data)
    try:
        callback(payload)
    except Exception:
        # Progress reporting must never change the repair outcome.
        return


@contextmanager
def observe_repair_progress(
    callback: ProgressCallback | None,
) -> Iterator[None]:
    """Install a progress observer for the current async context."""

    token = _progress_callback.set(callback)
    try:
        yield
    finally:
        _progress_callback.reset(token)
