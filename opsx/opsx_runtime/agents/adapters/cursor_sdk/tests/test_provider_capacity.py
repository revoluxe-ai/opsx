"""Cursor 'Server at capacity' is retried on create and on send, then a typed infra stop."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agents.abc_agent import raise_cursor_infra_failure
from agents.adapters.cursor_sdk.failure_classification import (
    _CAPACITY_BACKOFF_SECONDS,
    _CAPACITY_EXTRA_ATTEMPTS,
    call_with_provider_capacity_retry,
    is_cursor_provider_at_capacity,
    wait_with_provider_capacity_retry,
)
from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered
from workflows.stop_errors import VerifyStageStop

_CAPACITY = "internal: [unavailable] Server at capacity"


def test_provider_capacity_message_is_not_an_active_run() -> None:
    assert is_cursor_provider_at_capacity(RuntimeError(_CAPACITY))
    wrapped = RuntimeError("create failed")
    wrapped.__cause__ = RuntimeError(_CAPACITY)
    assert is_cursor_provider_at_capacity(wrapped)
    assert not is_cursor_provider_at_capacity(
        RuntimeError("InternalServerError: Agent already has active run"),
    )
    adapter_dir = Path(__file__).resolve().parents[1]
    for name in ("cursor_sdk_adapter.py", "repair_cursor_sdk_adapter.py"):
        text = (adapter_dir / name).read_text(encoding="utf-8")
        assert text.count("call_with_provider_capacity_retry") >= 2
        assert "_send_cursor_prompt" in text
        assert "wait_with_provider_capacity_retry" in text
        assert "_wait_accepted_run" in text
        assert "is_cursor_provider_at_capacity" in text


def test_provider_capacity_retries_then_typed_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повтор capacity и typed stop без pytest-asyncio: gate супервизора его не ставит."""

    async def _scenario() -> None:
        await _provider_capacity_retries_then_typed_stop(monkeypatch)

    asyncio.run(_scenario())


async def _provider_capacity_retries_then_typed_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def _sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.failure_classification.asyncio.sleep",
        _sleep,
    )
    calls = {"n": 0}

    async def _recovers() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError(_CAPACITY)
        return "agent"

    assert await call_with_provider_capacity_retry(_recovers) == "agent"
    assert calls["n"] == 3
    assert slept == list(_CAPACITY_BACKOFF_SECONDS[:2])

    attempts = {"n": 0}

    async def _stays_full() -> None:
        attempts["n"] += 1
        raise RuntimeError(_CAPACITY)

    with pytest.raises(RuntimeError, match="Server at capacity"):
        await call_with_provider_capacity_retry(_stays_full)
    assert attempts["n"] == _CAPACITY_EXTRA_ATTEMPTS + 1

    async def _active_run() -> None:
        raise RuntimeError("Agent already has active run")

    with pytest.raises(RuntimeError, match="already has active run"):
        await call_with_provider_capacity_retry(_active_run)

    ensure_adapter_factories_registered()
    with pytest.raises(VerifyStageStop) as err:
        raise_cursor_infra_failure(
            RuntimeError(_CAPACITY),
            state={"stream_label": "apply_red"},
        )
    assert err.value.code == "CURSOR_PROVIDER_AT_CAPACITY"
    assert err.value.owner == "infra"
    assert err.value.stage == "apply_red"
    assert "capacity" in err.value.next_action.lower()

    waits = {"n": 0}

    async def _wait_recovers() -> str:
        waits["n"] += 1
        if waits["n"] == 1:
            raise RuntimeError(_CAPACITY)
        return "same-run"

    assert await wait_with_provider_capacity_retry(_wait_recovers) == "same-run"
    assert waits["n"] == 2

    async def _stream_drop() -> None:
        raise RuntimeError("RemoteProtocolError: peer closed connection")

    with pytest.raises(RuntimeError, match="peer closed connection"):
        await wait_with_provider_capacity_retry(_stream_drop)
