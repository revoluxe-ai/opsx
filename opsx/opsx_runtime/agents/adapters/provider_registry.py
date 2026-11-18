"""Neutral registry for adapter construction (provider wires at import time)."""

from __future__ import annotations

from collections.abc import Callable
import os
from typing import Any

from agents.adapters.abc_adapter import ABCAdapter

_ReviewFactory = Callable[..., ABCAdapter]
_RepairFactory = Callable[..., ABCAdapter]
_ReconcileHooks: dict[str, dict[str, Callable[..., Any]]] = {}
_BootstrapHooks: dict[str, dict[str, Callable[..., Any]]] = {}

_DEFAULT_PROVIDER = "agents.adapters.cursor_sdk"
_ReviewFactories: dict[str, _ReviewFactory] = {}
_RepairFactories: dict[str, _RepairFactory] = {}


def _provider_key(provider_module: str | None = None) -> str:
    # The worker pins OPSX_ADAPTER_MODULE at launch; never use the last
    # imported adapter as implicit authority for the current execution.
    key = provider_module or os.environ.get("OPSX_ADAPTER_MODULE") or _DEFAULT_PROVIDER
    if not key.startswith("agents.adapters."):
        raise RuntimeError("EXECUTION_PROVIDER_UNAUTHORIZED")
    return key

# Exact provider identity: a Codex session must not acquire Cursor's bridge.
_ExecutionSessionFactories: dict[str, Callable[..., Any]] = {}
_ExecutionWorkspaceFactories: dict[str, Callable[..., Any]] = {}


def register_execution_workspace_factory(provider_module: str, factory: Callable[..., Any]) -> None:
    if provider_module not in {"agents.adapters.cursor_sdk", "agents.adapters.codex_app_server"}:
        raise RuntimeError("EXECUTION_WORKSPACE_PROVIDER_UNAUTHORIZED")
    if not callable(factory):
        raise TypeError("EXECUTION_WORKSPACE_FACTORY_INVALID")
    _ExecutionWorkspaceFactories[provider_module] = factory


def build_execution_workspace(provider_module: str, **kwargs: Any) -> Any:
    factory = _ExecutionWorkspaceFactories.get(provider_module)
    if factory is None:
        raise RuntimeError(f"EXECUTION_WORKSPACE_UNREGISTERED: {provider_module}")
    return factory(**kwargs)


def register_execution_session_factory(provider_module: str, factory: Callable[..., Any]) -> None:
    if not provider_module or not provider_module.startswith("agents.adapters."):
        raise ValueError("EXECUTION_PROVIDER_MODULE_INVALID")
    if not callable(factory):
        raise TypeError("EXECUTION_SESSION_FACTORY_INVALID")
    _ExecutionSessionFactories[provider_module] = factory


def build_execution_session(provider_module: str, **kwargs: Any) -> Any:
    factory = _ExecutionSessionFactories.get(provider_module)
    if factory is None:
        raise RuntimeError(f"EXECUTION_SESSION_UNREGISTERED: {provider_module}")
    return factory(**kwargs)


def register_review_adapter_factory(
    factory: _ReviewFactory, *, provider_module: str | None = None,
) -> None:
    """Bind a review constructor to exact provider identity."""
    _ReviewFactories[_provider_key(provider_module)] = factory


def register_repair_adapter_factory(
    factory: _RepairFactory, *, provider_module: str | None = None,
) -> None:
    """Bind a mutating constructor to exact provider identity."""
    _RepairFactories[_provider_key(provider_module)] = factory


def register_reconcile_hook(
    name: str, hook: Callable[..., Any], *, provider_module: str | None = None,
) -> None:
    _ReconcileHooks.setdefault(_provider_key(provider_module), {})[name] = hook


def register_bootstrap_hook(
    name: str, hook: Callable[..., Any], *, provider_module: str | None = None,
) -> None:
    _BootstrapHooks.setdefault(_provider_key(provider_module), {})[name] = hook


def call_bootstrap_hook(name: str, /, **kwargs: Any) -> Any:
    """Dispatch bootstrap only to the pinned provider, never last imported."""
    hook = _BootstrapHooks.get(_provider_key(), {}).get(name)
    if hook is None:
        raise RuntimeError(f"bootstrap hook {name!r} not registered")
    return hook(**kwargs)


def build_review_adapter(**kwargs: Any) -> ABCAdapter:
    provider = _provider_key()
    factory = _ReviewFactories.get(provider)
    if factory is None:
        raise RuntimeError(f"review adapter factory not registered: {provider}")
    return factory(**kwargs)


def build_repair_adapter(**kwargs: Any) -> ABCAdapter:
    provider = _provider_key()
    factory = _RepairFactories.get(provider)
    if factory is None:
        raise RuntimeError(f"repair adapter factory not registered: {provider}")
    return factory(**kwargs)


async def call_reconcile_hook(name: str, /, **kwargs: Any) -> Any:
    hook = reconcile_hook(name)
    if hook is None:
        raise RuntimeError(f"reconcile hook {name!r} not registered")
    return await hook(**kwargs)


def reconcile_hook(name: str) -> Callable[..., Any] | None:
    return _ReconcileHooks.get(_provider_key(), {}).get(name)


def factories_registered(provider_module: str | None = None) -> bool:
    provider = _provider_key(provider_module)
    return provider in _ReviewFactories and provider in _RepairFactories
