"""Neutral facade for shared bridge runtime (provider registers factory hook)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.adapters.provider_registry import reconcile_hook


def bridge_runtime_type() -> type:
    """Return bridge runtime class registered by the provider package."""
    hook = reconcile_hook("bridge_runtime_type")
    if hook is None:
        raise RuntimeError("bridge_runtime_type hook not registered")
    return hook()


def open_bridge_runtime(workspace: Path, **kwargs: Any) -> Any:
    """Construct async context manager for workspace-bound shared bridge."""
    return bridge_runtime_type()(workspace, **kwargs)
