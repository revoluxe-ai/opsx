"""Provider-side registration entry (import package ``__init__`` side effects)."""

from __future__ import annotations

from agents.adapters import provider_registry


def ensure_factories_registered() -> None:
    """Ensure registry hooks and adapter factories from this provider are present."""
    if provider_registry.factories_registered():
        return
    import agents.adapters.cursor_sdk  # noqa: F401 — registry side effects in __init__
