"""Inspect/normalize lane (deterministic)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agents.implementation.apply.visual_cover_asset_tool import audit_invest_yalta_cover_bundle


class ImageRenderPort:
    """Технический inspect registry covers (пилот visual)."""

    def __init__(self, *, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve()

    async def inspect_bundle(self) -> dict[str, object]:
        return await asyncio.to_thread(audit_invest_yalta_cover_bundle, self._repo_root)
