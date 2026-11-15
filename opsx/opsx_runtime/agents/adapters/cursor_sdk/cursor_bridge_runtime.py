"""Execution-scoped Cursor SDK bridge ownership (not an adapter concern).

One healthy bridge per runtime/workspace. Every SDK agent/run stays independent.
After a transport drop the broken generation is retired: existing runs must
reconcile by their original agent/run identities, never resend blindly.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from cursor_sdk.asyncio import AsyncClient

_LOG = logging.getLogger(__name__)


@dataclass(eq=False)
class _Generation:
    client: Any
    manager: Any
    leases: int = 0
    retired: bool = False
    closed: bool = False


class CursorBridgeRuntime:
    """Async-context-managed, workspace-bound shared bridge.

    Startup/teardown belong to the composition root. Adapters borrow clients
    through connection(), never close them. An exceptional connection failure
    retires its generation, but in-flight peers may finish and checkpoint.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        launch: Callable[..., Awaitable[Any]] | None = None,
        sdk_start_interval_seconds: float = 8.0,
        store_backend: str | None = None,
        store_scope: str | None = None,
    ) -> None:
        if not isinstance(sdk_start_interval_seconds, (int, float)) or not 0 <= sdk_start_interval_seconds <= 60:
            raise ValueError("CURSOR_SDK_START_INTERVAL_INVALID")
        from cursor_executor.bridge_local import selected_sdk_store_backend
        self.store_backend = store_backend if store_backend is not None else selected_sdk_store_backend()
        self.store_scope = store_scope
        # Validate before opening any bridge or dispatching a remote SDK run.
        if self.store_backend == "sqlite" and (
            not isinstance(store_scope, str) or not store_scope.strip() or len(store_scope) > 512
        ):
            raise ValueError("CURSOR_SQLITE_STORE_SCOPE_REQUIRED")
        if self.store_backend not in {"sqlite", "jsonl"}:
            raise ValueError("CURSOR_SDK_STORE_BACKEND_INVALID")
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("CURSOR_BRIDGE_WORKSPACE_MISSING")
        self._launch = launch
        self._lock = asyncio.Lock()
        # Shared across read-only and repair agents, including new bridge generations.
        # 8s between new SDK launches is <= 7.5/min, leaving headroom
        # under observed 30/min get_models quota for internal SDK requests.
        self._sdk_start_lock = asyncio.Lock()
        self._sdk_start_interval = float(sdk_start_interval_seconds)
        self._next_sdk_start = 0.0
        self._provider_paused = False
        self._generations: list[_Generation] = []
        self._current: _Generation | None = None
        self._started = False
        self._closing = False
        self._workspace_key: str | None = None

    def _config(self, workspace: Path) -> dict[str, Any]:
        # Import lazily because these helpers also own local shell-env policy.
        from cursor_executor.bridge_local import bridge_launch_kwargs
        return bridge_launch_kwargs(
            agent_cwd=str(workspace),
            store_backend=self.store_backend,
            store_scope=self.store_scope,
        )

    def agent_local_options(self, *, agent_cwd: str) -> dict[str, Any]:
        """Ensure adapter resumes against the same store as this bridge."""
        from cursor_executor.bridge_local import agent_local_options
        return agent_local_options(
            agent_cwd=agent_cwd,
            store_backend=self.store_backend,
            store_scope=self.store_scope,
        )

    async def _open(self) -> _Generation:
        options = self._config(self.workspace)
        if self._workspace_key is None:
            self._workspace_key = str(options["workspace"])
        launch = self._launch or AsyncClient.launch_bridge
        manager = await launch(**options)
        # Cursor SDK launch_bridge returns an async context manager.
        try:
            client = await manager.__aenter__()
        except BaseException:
            await manager.__aexit__(None, None, None)
            raise
        generation = _Generation(client=client, manager=manager)
        self._generations.append(generation)
        self._current = generation
        _LOG.info("cursor_bridge_started workspace=%s generation=%d", self._workspace_key, len(self._generations))
        return generation

    async def __aenter__(self) -> "CursorBridgeRuntime":
        async with self._lock:
            if self._started or self._closing:
                raise RuntimeError("CURSOR_BRIDGE_RUNTIME_ALREADY_USED")
            self._started = True
            try:
                await self._open()
            except BaseException:
                self._closing = True
                raise
        return self

    async def _close_generation(self, generation: _Generation) -> None:
        if generation.closed:
            return
        generation.closed = True
        await generation.manager.__aexit__(None, None, None)

    async def __aexit__(self, *_exc: Any) -> None:
        async with self._lock:
            self._closing = True
            generations = list(reversed(self._generations))
            for generation in generations:
                generation.retired = True
            self._current = None
        # Teardown is not conditional on clean workflow completion. It closes
        # all generations even when another bridge's teardown raises.
        failures: list[BaseException] = []
        for generation in generations:
            try:
                await self._close_generation(generation)
            except BaseException as error:
                failures.append(error)
        if failures and _exc[0] is None:
            raise RuntimeError("CURSOR_BRIDGE_SHUTDOWN_FAILED") from failures[0]

    def _assert_workspace(self, workspace: Path) -> None:
        candidate = Path(workspace).resolve()
        if not candidate.is_dir():
            raise ValueError("CURSOR_BRIDGE_WORKSPACE_MISSING")
        options = self._config(candidate)
        if str(options["workspace"]) != self._workspace_key:
            raise ValueError("CURSOR_BRIDGE_WORKSPACE_MISMATCH")

    @asynccontextmanager
    async def connection(self, workspace: Path) -> AsyncIterator[Any]:
        """Borrow an active client. A stopped runtime never launches ad hoc."""
        async with self._lock:
            # Lifecycle takes precedence: _workspace_key is not bound before
            # __aenter__ and must not produce a spurious WORKSPACE_MISMATCH.
            if not self._started or self._closing:
                raise RuntimeError("CURSOR_BRIDGE_RUNTIME_INACTIVE")
            self._assert_workspace(workspace)
            generation = self._current or await self._open()
            generation.leases += 1
        try:
            yield generation.client
        finally:
            close_now = False
            async with self._lock:
                generation.leases -= 1
                if generation.leases == 0 and generation.retired and not generation.closed:
                    close_now = True
            if close_now:
                await self._close_generation(generation)

    async def throttle_sdk_start(self) -> None:
        """Bound new SDK agent creation across all adapters in this execution.

        This is pacing, NOT a guarantee against provider-wide rate limits:
        other executions or the SDK itself can consume the same quota.
        Resume of an already-sent run must not consume or bypass this gate.
        """
        async with self._sdk_start_lock:
            if not self._started or self._closing:
                raise RuntimeError("CURSOR_BRIDGE_RUNTIME_INACTIVE")
            if self._provider_paused:
                raise RuntimeError("CURSOR_PROVIDER_OVERLOAD_PAUSE")
            delay = max(0.0, self._next_sdk_start - time.monotonic())
            if delay:
                _LOG.info("cursor_sdk_launch_paced delay_seconds=%.2f", delay)
                await asyncio.sleep(delay)
            if self._closing:
                raise RuntimeError("CURSOR_BRIDGE_RUNTIME_INACTIVE")
            if self._provider_paused:
                raise RuntimeError("CURSOR_PROVIDER_OVERLOAD_PAUSE")
            self._next_sdk_start = time.monotonic() + self._sdk_start_interval

    def pause_provider(self) -> None:
        """Fail closed for this execution after provider overload is observed."""
        self._provider_paused = True
        _LOG.warning("cursor_sdk_provider_overload_paused workspace=%s", self._workspace_key)

    async def invalidate(self, client: Any) -> None:
        """Retire a broken stream's bridge, without interrupting peers.

        Next borrower opens one replacement. An old bridge closes when its
        final borrower exits; overlap during reconnection is transient.
        """
        close_now: _Generation | None = None
        async with self._lock:
            for generation in self._generations:
                if generation.client is client and not generation.retired:
                    generation.retired = True
                    if self._current is generation:
                        self._current = None
                    if generation.leases == 0:
                        close_now = generation
                    _LOG.warning("cursor_bridge_retired workspace=%s", self._workspace_key)
                    break
        if close_now is not None:
            await self._close_generation(close_now)
