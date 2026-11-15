"""Neutral registry for adapter construction (provider wires at import time)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents.adapters.abc_adapter import ABCAdapter

_ReviewFactory = Callable[..., ABCAdapter]
_RepairFactory = Callable[..., ABCAdapter]
_ReconcileHooks: dict[str, Callable[..., Any]] = {}
_BootstrapHooks: dict[str, Callable[..., Any]] = {}

_review_factory: _ReviewFactory | None = None
_repair_factory: _RepairFactory | None = None


def register_review_adapter_factory(factory: _ReviewFactory) -> None:
    """Register review adapter constructor (called from provider package ``__init__``)."""
    global _review_factory
    _review_factory = factory


def register_repair_adapter_factory(factory: _RepairFactory) -> None:
    """Register mutating repair adapter constructor."""
    global _repair_factory
    _repair_factory = factory


def register_reconcile_hook(name: str, hook: Callable[..., Any]) -> None:
    """Register reconcile helper by neutral name (``launch_bridge_client``, etc.)."""
    _ReconcileHooks[name] = hook


def register_bootstrap_hook(name: str, hook: Callable[..., Any]) -> None:
    """Register provider runtime bootstrap hook (``ensure_wheel_importable``, etc.)."""
    _BootstrapHooks[name] = hook


def call_bootstrap_hook(name: str, /, **kwargs: Any) -> Any:
    """Invoke bootstrap hook registered by the provider package."""
    hook = _BootstrapHooks.get(name)
    if hook is None:
        raise RuntimeError(f"bootstrap hook {name!r} not registered")
    return hook(**kwargs)


def build_review_adapter(**kwargs: Any) -> ABCAdapter:
    """Build review adapter; provider package must be imported first."""
    if _review_factory is None:
        raise RuntimeError(
            "review adapter factory not registered; import provider adapter package"
        )
    return _review_factory(**kwargs)


def build_repair_adapter(**kwargs: Any) -> ABCAdapter:
    """Build mutating repair adapter; provider package must be imported first."""
    if _repair_factory is None:
        raise RuntimeError(
            "repair adapter factory not registered; import provider adapter package"
        )
    return _repair_factory(**kwargs)


async def call_reconcile_hook(name: str, /, **kwargs: Any) -> Any:
    """Invoke reconcile hook registered by the provider package."""
    hook = _ReconcileHooks.get(name)
    if hook is None:
        raise RuntimeError(f"reconcile hook {name!r} not registered")
    return await hook(**kwargs)


def reconcile_hook(name: str) -> Callable[..., Any] | None:
    """Return reconcile hook for sync helpers (e.g. ``async_client_type``)."""
    return _ReconcileHooks.get(name)


def factories_registered() -> bool:
    """True when review and repair adapter factories are wired."""
    return _review_factory is not None and _repair_factory is not None
