"""Shared Cursor bridge для Owner-named repair (parity с verify-impl sqlite store).

Эфемерный ``launch_bridge`` без ``CursorBridgeRuntime`` пишет в глобальный
``tools/cursor_executor/.bridge-state/jsonl-store`` (``checkpoints.ndjson``,
``run_events.ndjson``). Verify-impl и ``BridgeContainer`` используют
``store_backend=sqlite`` и scoped ``index.db``.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


def owner_named_bridge_store_scope(change_id: str) -> str:
    """Стабильный recovery scope UI repair (как ``verify_impl:{change_id}``).

    Args:
        change_id: Slug каталога run (форма ``change`` или дефолт ``owner-named``).

    Returns:
        Строка scope для SQLite agent store.
    """
    text = change_id.strip().lower()
    text = re.sub(r"[^a-z0-9._+-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    slug = text or "owner-named"
    return f"owner_named_bug_repair:{slug}"


@asynccontextmanager
async def owner_named_cursor_bridge(
    repo_root: Path,
    *,
    change_id: str,
) -> AsyncIterator[Any]:
    """Открыть ``CursorBridgeRuntime`` (sqlite), не jsonl bridge log.

    Yields:
        ``CursorBridgeRuntime`` для ``RepairAdapter(bridge_runtime=…)``.
    """
    from agents.adapters.bridge_runtime_port import open_bridge_runtime

    scope = owner_named_bridge_store_scope(change_id)
    async with open_bridge_runtime(
        repo_root.resolve(),
        store_backend="sqlite",
        store_scope=scope,
    ) as bridge:
        yield bridge