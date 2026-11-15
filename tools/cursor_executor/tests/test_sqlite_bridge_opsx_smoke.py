"""Smoke: opsx bridge launcher opens sqlite under repo-local cursor-sdk dir."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    import cursor_sdk  # noqa: F401
except ImportError:
    cursor_sdk = None  # type: ignore[assignment]


@unittest.skipIf(cursor_sdk is None, "cursor-sdk not installed")
class SqliteBridgeOpsxSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_creates_index_db_in_scoped_root(self) -> None:
        from cursor_executor.bridge_local import (
            bridge_launch_kwargs,
            cursor_sdk_sqlite_store_root,
        )
        from cursor_sdk.asyncio import AsyncClient

        game = Path(__file__).resolve().parents[3]
        scope = "opsx-smoke:sqlite-bridge"
        store_root = cursor_sdk_sqlite_store_root(game, scope)
        bridge_tmp = game / "opsx" / "runs" / "cursor-sdk-bridge-tmp"
        bridge_tmp.mkdir(parents=True, exist_ok=True)
        if store_root.is_dir():
            shutil.rmtree(store_root, ignore_errors=True)
        launch = bridge_launch_kwargs(
            agent_cwd=str(game),
            store_backend="sqlite",
            store_scope=scope,
        )
        self.assertEqual(launch["state_root"], os.fspath(store_root))
        prev = {
            key: os.environ.get(key)
            for key in ("CURSOR_SDK_BRIDGE_BIN", "TEMP", "TMP", "TMPDIR")
        }
        os.environ["TEMP"] = os.fspath(bridge_tmp)
        os.environ["TMP"] = os.fspath(bridge_tmp)
        os.environ["TMPDIR"] = os.fspath(bridge_tmp)
        try:
            async with await AsyncClient.launch_bridge(**launch) as client:
                _ = client
                await asyncio.sleep(0.2)
        finally:
            for key, value in prev.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        index_db = store_root / "index.db"
        self.assertTrue(
            index_db.is_file(),
            f"expected sqlite index at {index_db}",
        )


if __name__ == "__main__":
    unittest.main()
