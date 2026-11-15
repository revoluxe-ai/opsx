"""Neutral facade for offline reconcile billing refresh (provider hook)."""

from __future__ import annotations

from typing import Any

from agents.adapters.provider_registry import call_reconcile_hook


async def refresh_saved_run_billing(
    metadata: dict[str, Any], *, retries: int = 3
) -> None:
    """Refresh server-reported billing for a finished run via provider hook."""
    await call_reconcile_hook(
        "refresh_saved_run_billing",
        metadata=metadata,
        retries=retries,
    )
