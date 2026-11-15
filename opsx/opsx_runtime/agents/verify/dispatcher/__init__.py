"""Dispatcher: маршрутизация repair unit / findings в workflow subgraphs."""

from __future__ import annotations

__all__ = [
    "build_dispatcher_pass_queue",
    "dispatcher_prepare",
    "dispatcher_record_outcome",
    "dispatcher_route_edge",
]


def __getattr__(name: str):
    """Lazy exports — избегаем циклических импортов с ``dispatch_unit``."""
    if name == "build_dispatcher_pass_queue":
        from agents.verify.dispatcher.queue import build_dispatcher_pass_queue

        return build_dispatcher_pass_queue
    if name in ("dispatcher_prepare", "dispatcher_record_outcome", "dispatcher_route_edge"):
        from agents.verify.dispatcher import agent as _agent

        return getattr(_agent, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
