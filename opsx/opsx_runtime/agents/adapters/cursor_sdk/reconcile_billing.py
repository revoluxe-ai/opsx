"""Обновление billing по сохранённому agent_id (единственная точка wheel asyncio)."""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_sdk.asyncio import AsyncAgent, AsyncClient

_REPO_ROOT = project_root()
_EXECUTOR_ROOT = resource_path(_REPO_ROOT, "tools", "cursor_executor")
if str(_EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXECUTOR_ROOT))

from cursor_executor.auth import resolve_auth  # noqa: E402
from cursor_executor.bridge_local import (  # noqa: E402
    agent_local_options,
    bridge_launch_kwargs,
)


async def refresh_saved_run_billing(
    metadata: dict[str, Any], *, retries: int = 3
) -> None:
    """Запросить ``get_usage`` у живого агента без нового model turn."""
    if not metadata.get("agent_id"):
        return
    auth = resolve_auth()
    if not auth.api_key:
        metadata["billing_error"] = "CURSOR_API_KEY missing"
        return
    workspace = str(metadata.get("fixture") or metadata.get("workspace"))
    try:
        async with await AsyncClient.launch_bridge(
            **bridge_launch_kwargs(agent_cwd=workspace)
        ) as client:
            agent = await AsyncAgent.resume(
                str(metadata["agent_id"]),
                {
                    "apiKey": auth.api_key,
                    "local": agent_local_options(agent_cwd=workspace),
                    "model": metadata["model"],
                },
                client=client,
            )
            for attempt in range(retries):
                billed = await asyncio.wait_for(agent.get_usage(), timeout=12)
                cost = billed.cost
                if cost is None and len(billed.runs) == 1:
                    cost = billed.runs[0].cost
                if cost is not None:
                    metadata["cost"] = {
                        "raw_cost_cents": cost.raw_cost_cents,
                        "charged_cents": cost.charged_cents,
                    }
                    metadata["cost_status"] = "cursor_reported"
                    metadata["billing_checked_at"] = (
                        datetime.now(timezone.utc).isoformat()
                    )
                    return
                metadata["cost_status"] = "pending"
                if attempt < retries - 1:
                    await asyncio.sleep(3)
            metadata["billing_checked_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        metadata["billing_error"] = f"{type(exc).__name__}: {exc}"
        metadata["cost_status"] = (
            "unavailable" if "feature_unavailable" in str(exc) else "pending"
        )
