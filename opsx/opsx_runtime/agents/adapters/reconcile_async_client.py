"""Neutral reconcile async client facade (hooks registered by provider package)."""

from __future__ import annotations

from typing import Any

from agents.adapters.provider_registry import call_reconcile_hook, reconcile_hook


async def launch_bridge_client(**launch: Any) -> Any:
    """Launch ephemeral bridge client via provider hook."""
    return await call_reconcile_hook("launch_bridge_client", **launch)


def async_client_type() -> type:
    """Async client type for reconcile tests (provider hook)."""
    hook = reconcile_hook("async_client_type")
    if hook is None:
        raise RuntimeError("async_client_type hook not registered")
    return hook()
