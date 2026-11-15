"""A3–A8, A7: ensure_cursor_session heartbeat, cancel, and pre-retry cleanup."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest
from temporalio.testing import ActivityEnvironment

from activities import (
    ActivityConfig,
    _workflow_key,
    configure_activities,
    ensure_cursor_session,
)


@pytest.mark.asyncio
async def test_heartbeat_wrapper_propagates_activity_context_to_worker_thread(
    monkeypatch,
) -> None:
    """Bootstrap calls activity.info() off-thread; executor must copy contextvars."""
    configure_activities(ActivityConfig(mode="real", repo_path="."))
    workflow_ids_in_thread: list[str] = []

    def _bootstrap(ctx):
        workflow_ids_in_thread.append(_workflow_key())
        ctx["execution_id"] = "wf:ep:agent-bootstrap"

        class _Adapter:
            def session_id(self):
                return "agent-ctx"

        return _Adapter()

    monkeypatch.setattr("activities._bootstrap_session_adapter", _bootstrap)

    class _Client:
        def cancel_bootstrap_executions(self, **_kw):
            return []

        def cancel_execution(self, *_a, **_k):
            return True

    monkeypatch.setattr("activities._process_client_for_cancel", lambda: _Client())

    env = ActivityEnvironment()
    with patch("activities.activity.heartbeat"):
        result = await env.run(ensure_cursor_session, {})

    assert result["session_id"] == "agent-ctx"
    assert len(workflow_ids_in_thread) == 1
    assert workflow_ids_in_thread[0]


@pytest.mark.asyncio
async def test_real_mode_uses_heartbeat_wrapper_not_bare_to_thread(monkeypatch) -> None:
    configure_activities(ActivityConfig(mode="real", repo_path="."))
    to_thread_calls: list = []

    async def _track_to_thread(fn):
        to_thread_calls.append(fn)
        return fn()

    monkeypatch.setattr(asyncio, "to_thread", _track_to_thread)
    hb_calls: list = []

    async def _fake_hb(fn, **kwargs):
        hb_calls.append(kwargs.get("details"))
        return fn()

    monkeypatch.setattr("activities._run_blocking_with_heartbeat", _fake_hb)

    class _Adapter:
        def session_id(self):
            return "agent-fake"

    monkeypatch.setattr(
        "activities._bootstrap_session_adapter",
        lambda ctx: _Adapter(),
    )

    class _Client:
        def cancel_bootstrap_executions(self, **_kw):
            return []

        def cancel_execution(self, *_a, **_k):
            return True

    monkeypatch.setattr("activities._process_client_for_cancel", lambda: _Client())

    env = ActivityEnvironment()
    result = await env.run(ensure_cursor_session, {"mode": "real", "repo_path": "."})
    assert result["session_id"] == "agent-fake"
    assert to_thread_calls == []
    assert hb_calls


@pytest.mark.asyncio
async def test_bootstrap_emits_multiple_heartbeats_a4(monkeypatch) -> None:
    configure_activities(ActivityConfig(mode="real", repo_path="."))
    heartbeats: list = []

    def _slow_bootstrap(ctx):
        time.sleep(0.12)
        ctx["execution_id"] = "wf:ep:agent-bootstrap"

        class _Adapter:
            def session_id(self):
                return "agent-slow"

        return _Adapter()

    monkeypatch.setattr("activities._bootstrap_session_adapter", _slow_bootstrap)

    class _Client:
        def cancel_bootstrap_executions(self, **_kw):
            return []

        def cancel_execution(self, *_a, **_k):
            return True

    monkeypatch.setattr("activities._process_client_for_cancel", lambda: _Client())

    original_interval = 30.0
    monkeypatch.setattr("activities._HEARTBEAT_INTERVAL_SECONDS", 0.04)

    env = ActivityEnvironment()
    with patch("activities.activity.heartbeat", side_effect=lambda d: heartbeats.append(d)):
        await env.run(ensure_cursor_session, {})

    assert len(heartbeats) >= 2


@pytest.mark.asyncio
async def test_pre_retry_cancel_bootstrap_executions_a8(monkeypatch) -> None:
    configure_activities(ActivityConfig(mode="real", repo_path="."))
    swept: list = []

    class _Client:
        def cancel_bootstrap_executions(self, *, correlation_id, suffix):
            swept.append((correlation_id, suffix))
            return ["old-bootstrap"]

        def cancel_execution(self, *_a, **_k):
            return True

    monkeypatch.setattr("activities._process_client_for_cancel", lambda: _Client())
    monkeypatch.setattr(
        "activities._bootstrap_session_adapter",
        lambda ctx: type("A", (), {"session_id": lambda self: "agent-x"})(),
    )

    env = ActivityEnvironment()
    await env.run(ensure_cursor_session, {})
    assert swept and swept[0][1] == "agent-bootstrap"


@pytest.mark.asyncio
async def test_cancel_hook_invoked_once_on_activity_cancel_a7(monkeypatch) -> None:
    configure_activities(ActivityConfig(mode="real", repo_path="."))
    hook_calls: list = []

    async def _fake_hb(fn, *, cancel_hook=None, **kwargs):
        out = fn()
        if cancel_hook:
            cancel_hook()
        return out

    monkeypatch.setattr("activities._run_blocking_with_heartbeat", _fake_hb)

    class _Client:
        def cancel_bootstrap_executions(self, **_kw):
            return []

        def cancel_execution(self, eid, reason=""):
            hook_calls.append((eid, reason))
            return True

    monkeypatch.setattr("activities._process_client_for_cancel", lambda: _Client())

    def _bootstrap(ctx):
        ctx["execution_id"] = "exec-cancel-test"
        return type("A", (), {"session_id": lambda self: "agent-c"})()

    monkeypatch.setattr("activities._bootstrap_session_adapter", _bootstrap)

    env = ActivityEnvironment()
    await env.run(ensure_cursor_session, {})
    assert len([c for c in hook_calls if c[1] == "activity_cancelled"]) == 1
