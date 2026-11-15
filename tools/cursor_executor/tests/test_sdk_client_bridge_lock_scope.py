"""Bridge store lock must not span long SDK run waits (concurrent bootstrap/review)."""

from __future__ import annotations

import asyncio
import threading
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from cursor_executor.bridge_local import acquire_bridge_store_lock, release_bridge_store_lock
from cursor_executor.schemas import ModelRoute, WritePolicy
from cursor_executor.sdk_client import AsyncCursorSdkClient


class BridgeLockScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_on_existing_agent_releases_lock_during_wait(self) -> None:
        """Regression: a 25m review run must not block catalog/bootstrap for 300s."""
        wait_entered = threading.Event()
        wait_release = threading.Event()
        lock_acquired_during_wait = threading.Event()

        async def _slow_wait(_run: Any, _timeout: float | None, *, cancel_remote: bool) -> Any:
            wait_entered.set()

            def _probe() -> None:
                handle = acquire_bridge_store_lock(timeout_seconds=0.2)
                release_bridge_store_lock(handle)
                lock_acquired_during_wait.set()

            threading.Thread(target=_probe, daemon=True).start()
            await asyncio.to_thread(wait_release.wait, 5.0)
            result = MagicMock()
            result.id = "run-1"
            result.agent_id = "agent-1"
            result.status = "finished"
            result.result = "ok"
            result.duration_ms = 1
            result.model = None
            return result

        mock_client = AsyncMock()
        mock_run = MagicMock()
        mock_agent = MagicMock()
        mock_agent.send = AsyncMock(return_value=mock_run)

        with (
            patch(
                "cursor_executor.sdk_client._launch_bridge_with_owned_temp",
                new=AsyncMock(return_value=mock_client),
            ),
            patch("cursor_sdk.asyncio.AsyncAgent.resume", new=AsyncMock(return_value=mock_agent)),
            patch("cursor_executor.sdk_client._wait_run_result", new=_slow_wait),
        ):
            client = AsyncCursorSdkClient()
            task = asyncio.create_task(
                client.send_on_existing_agent(
                    api_key="key",
                    workspace="/workspace",
                    agent_id="agent-1",
                    prompt="hi",
                    write_policy=WritePolicy.READ_ONLY,
                    timeout_seconds=60.0,
                    model_route=ModelRoute(model_id="composer-2.5", params={}),
                )
            )
            await asyncio.to_thread(wait_entered.wait, 5.0)
            self.assertTrue(
                lock_acquired_during_wait.wait(timeout=2.0),
                "bridge store lock should be free while waiting on SDK run",
            )
            wait_release.set()
            outcome = await task

        self.assertEqual(outcome.result_text, "ok")
        mock_client.aclose.assert_awaited()


if __name__ == "__main__":
    unittest.main()
