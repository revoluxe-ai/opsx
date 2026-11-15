"""Deterministic shared Cursor bridge lifecycle and dependency injection tests.

No live SDK or network credentials. Real ten-stream load requires a separate
live acceptance run; these tests prove local lifecycle/authority invariants.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest

from agents.adapters.cursor_sdk.cursor_bridge_runtime import CursorBridgeRuntime
from agents.adapters.cursor_sdk.cursor_sdk_adapter import CursorSDKAdapter
from workflows.repair.repair_adapter_port import RepairAdapter


class FakeBridge:
    def __init__(self, number, events, *, enter_error=False, exit_error=False):
        self.client = object()
        self.number = number
        self.events = events
        self.enter_error = enter_error
        self.exit_error = exit_error

    async def __aenter__(self):
        self.events.append(f"enter:{self.number}")
        if self.enter_error:
            raise RuntimeError("simulated startup failure")
        return self.client

    async def __aexit__(self, *_exc):
        self.events.append(f"exit:{self.number}")
        if self.exit_error:
            raise RuntimeError("simulated shutdown failure")


def setup_launch(monkeypatch):
    events = []
    launches = []

    async def launch(**kwargs):
        assert "workspace" in kwargs
        item = FakeBridge(len(launches) + 1, events)
        launches.append(item)
        return item

    monkeypatch.setattr(
        CursorBridgeRuntime, "_config",
        lambda self, workspace: {"workspace": str(workspace.resolve())},
    )
    return events, launches, launch


@pytest.mark.asyncio
async def test_ten_parallel_agents_two_adapters_borrow_one_bridge(tmp_path, monkeypatch):
    events, launches, launch = setup_launch(monkeypatch)
    ready = asyncio.Event()
    release = asyncio.Event()
    clients = []
    active = 0

    async def job(adapter):
        nonlocal active
        async with adapter._connection(tmp_path) as client:
            clients.append(client)
            active += 1
            if active == 10:
                ready.set()
            await release.wait()

    async with CursorBridgeRuntime(tmp_path, launch=launch) as bridge:
        read_adapter = CursorSDKAdapter(bridge_runtime=bridge)
        repair_adapter = RepairAdapter(bridge_runtime=bridge)
        tasks = [
            asyncio.create_task(job(read_adapter if i % 2 == 0 else repair_adapter))
            for i in range(10)
        ]
        await asyncio.wait_for(ready.wait(), timeout=4)
        assert len(launches) == 1
        assert len(clients) == 10
        assert len({id(x) for x in clients}) == 1
        assert events == ["enter:1"]
        release.set()
        await asyncio.gather(*tasks)
    assert events == ["enter:1", "exit:1"]


@pytest.mark.asyncio
async def test_reconnect_drains_old_generation_without_killing_peer(tmp_path, monkeypatch):
    events, launches, launch = setup_launch(monkeypatch)
    async with CursorBridgeRuntime(tmp_path, launch=launch) as bridge:
        async with bridge.connection(tmp_path) as old:
            async with bridge.connection(tmp_path) as peer:
                assert old is peer
                await bridge.invalidate(old)
                async with bridge.connection(tmp_path) as new:
                    assert new is not old
                    assert len(launches) == 2
                    assert "exit:1" not in events
                assert "exit:1" not in events
            assert "exit:1" not in events
        assert events.count("exit:1") == 1
    assert events.count("exit:1") == 1
    assert events.count("exit:2") == 1


@pytest.mark.asyncio
async def test_workspace_authority_fails_closed(tmp_path, monkeypatch):
    _, launches, launch = setup_launch(monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    async with CursorBridgeRuntime(tmp_path, launch=launch) as bridge:
        with pytest.raises(ValueError, match="WORKSPACE_MISMATCH"):
            async with bridge.connection(other):
                pytest.fail("wrong workspace acquired a client")
        assert len(launches) == 1


@pytest.mark.asyncio
async def test_use_before_start_and_after_close_fails(tmp_path, monkeypatch):
    events, launches, launch = setup_launch(monkeypatch)
    bridge = CursorBridgeRuntime(tmp_path, launch=launch)
    with pytest.raises(RuntimeError, match="CURSOR_BRIDGE_RUNTIME_INACTIVE"):
        async with bridge.connection(tmp_path):
            pass
    assert launches == []  # no bridge startup on an invalid lifecycle
    async with bridge:
        async with bridge.connection(tmp_path):
            pass
    assert events == ["enter:1", "exit:1"]
    with pytest.raises(RuntimeError, match="CURSOR_BRIDGE_RUNTIME_INACTIVE"):
        async with bridge.connection(tmp_path):
            pass
    assert len(launches) == 1  # closed manager cannot launch again


@pytest.mark.asyncio
async def test_two_bridge_contexts_close_on_exception_reverse_order(tmp_path, monkeypatch):
    events, _, launch = setup_launch(monkeypatch)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    with pytest.raises(ValueError, match="workflow failed"):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(CursorBridgeRuntime(first, launch=launch))
            await stack.enter_async_context(CursorBridgeRuntime(second, launch=launch))
            raise ValueError("workflow failed")
    assert events == ["enter:1", "enter:2", "exit:2", "exit:1"]


@pytest.mark.asyncio
async def test_second_bridge_enter_failure_closes_first(tmp_path, monkeypatch):
    events, _, launch = setup_launch(monkeypatch)
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    times = 0

    async def possibly_failing_launch(**kwargs):
        nonlocal times
        times += 1
        return FakeBridge(times, events, enter_error=times == 2)

    with pytest.raises(RuntimeError, match="startup failure"):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(CursorBridgeRuntime(first, launch=possibly_failing_launch))
            await stack.enter_async_context(CursorBridgeRuntime(second, launch=possibly_failing_launch))
    assert "exit:1" in events
    assert "exit:2" in events


@pytest.mark.asyncio
async def test_bridge_teardown_failure_still_closes_all_generations(tmp_path, monkeypatch):
    events, _, launch = setup_launch(monkeypatch)

    async def failing_launch(**kwargs):
        b = await launch(**kwargs)
        b.exit_error = b.number == 2
        return b

    with pytest.raises(RuntimeError, match="SHUTDOWN_FAILED"):
        async with CursorBridgeRuntime(tmp_path, launch=failing_launch) as bridge:
            async with bridge.connection(tmp_path) as old:
                await bridge.invalidate(old)
            async with bridge.connection(tmp_path):
                pass
    assert "exit:1" in events and "exit:2" in events


def test_abc_adapter_interface_not_changed():
    from agents.adapters.abc_adapter import ABCAdapter

    assert not hasattr(ABCAdapter, "bridge_runtime")
    assert not hasattr(ABCAdapter, "_connection")



@pytest.mark.asyncio
async def test_shared_bridge_paces_fresh_sdk_launches_across_ten_callers(
    tmp_path, monkeypatch,
):
    import time

    _, launches, launch = setup_launch(monkeypatch)
    started = []
    async with CursorBridgeRuntime(
        tmp_path, launch=launch, sdk_start_interval_seconds=0.02,
    ) as bridge:
        async def start_one():
            await bridge.throttle_sdk_start()
            started.append(time.monotonic())
        await asyncio.gather(*(start_one() for _ in range(10)))
    assert len(launches) == 1
    assert len(started) == 10
    assert started[-1] - started[0] >= 0.15
    assert all(b - a >= 0.012 for a, b in zip(started, started[1:]))


@pytest.mark.asyncio
async def test_provider_pause_rejects_queued_sdk_launches_without_replaying(
    tmp_path, monkeypatch,
):
    _, launches, launch = setup_launch(monkeypatch)
    async with CursorBridgeRuntime(
        tmp_path, launch=launch, sdk_start_interval_seconds=0.10,
    ) as bridge:
        await bridge.throttle_sdk_start()
        queued = asyncio.create_task(bridge.throttle_sdk_start())
        await asyncio.sleep(0)
        bridge.pause_provider()
        with pytest.raises(RuntimeError, match="CURSOR_PROVIDER_OVERLOAD_PAUSE"):
            await queued
        with pytest.raises(RuntimeError, match="CURSOR_PROVIDER_OVERLOAD_PAUSE"):
            await bridge.throttle_sdk_start()
    assert len(launches) == 1


def test_invalid_start_rate_limit_configuration_is_rejected(tmp_path):
    for invalid in (-1, 61, None, "fast"):
        with pytest.raises((TypeError, ValueError)):
            CursorBridgeRuntime(tmp_path, sdk_start_interval_seconds=invalid)



@pytest.mark.asyncio
async def test_inactive_lifecycle_precedes_workspace_key_validation(tmp_path, monkeypatch):
    _, launches, launch = setup_launch(monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    bridge = CursorBridgeRuntime(tmp_path, launch=launch)

    with pytest.raises(RuntimeError, match="CURSOR_BRIDGE_RUNTIME_INACTIVE"):
        async with bridge.connection(other):
            pass
    assert launches == []

    async with bridge:
        # Once started, the original workspace authority still applies.
        with pytest.raises(ValueError, match="CURSOR_BRIDGE_WORKSPACE_MISMATCH"):
            async with bridge.connection(other):
                pass
        async with bridge.connection(tmp_path):
            pass

    with pytest.raises(RuntimeError, match="CURSOR_BRIDGE_RUNTIME_INACTIVE"):
        async with bridge.connection(other):
            pass
    assert len(launches) == 1


@pytest.mark.asyncio
async def test_scoped_sqlite_bridge_and_adapters_share_store(tmp_path, monkeypatch):
    # Hermetic store: never inspect the developer's persisted JSONL sessions.
    # The production legacy-state guard is tested separately.
    local_store_package = tmp_path / "cursor-executor"
    local_store_package.mkdir()
    monkeypatch.setattr(
        "cursor_executor.bridge_local.executor_package_root",
        lambda: local_store_package,
    )
    launches = []
    events = []

    async def launch(**kwargs):
        launches.append(kwargs)
        return FakeBridge(len(launches), events)

    async with CursorBridgeRuntime(
        tmp_path, launch=launch, store_backend="sqlite",
        store_scope="verify_impl:change-id",
    ) as bridge:
        read = CursorSDKAdapter(bridge_runtime=bridge)
        repair = RepairAdapter(bridge_runtime=bridge)
        store = launches[0]["local"]["store"]
        assert store["type"] == "sqlite"
        assert store["rootDir"] == launches[0]["state_root"]
        assert read.bridge_runtime.agent_local_options(
            agent_cwd=str(tmp_path),
        )["store"] == store
        assert repair.bridge_runtime.agent_local_options(
            agent_cwd=str(tmp_path),
        )["store"] == store
        assert "opsx/runs/cursor-sdk" in launches[0]["state_root"].replace("\\", "/")
        async with read._connection(tmp_path) as first:
            async with repair._connection(tmp_path) as second:
                assert first is second
    assert events == ["enter:1", "exit:1"]


def test_scoped_sqlite_bridge_rejects_missing_scope_before_launch(tmp_path):
    with pytest.raises(ValueError, match="CURSOR_SQLITE_STORE_SCOPE_REQUIRED"):
        CursorBridgeRuntime(tmp_path, store_backend="sqlite")
    with pytest.raises(ValueError, match="CURSOR_SDK_STORE_BACKEND_INVALID"):
        CursorBridgeRuntime(tmp_path, store_backend="other")
