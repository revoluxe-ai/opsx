"""Тесты политики resume при обрыве Cursor bridge-stream."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.abc_agent import (
    ABCAgent,
    is_cursor_bridge_stream_drop,
    is_cursor_provider_access_denied,
    is_cursor_resume_target_gone,
    log_cursor_bridge_stream_drop,
)
from agents.adapters.cursor_sdk.cursor_sdk_adapter import CursorSDKAdapter
from agents.adapters.provider_errors import BadRequestError, NetworkError
from agents.adapters.provider_run_types import RunResult


def test_is_cursor_bridge_stream_drop_network_error() -> None:
    exc = NetworkError(
        "Bridge request failed: RemoteProtocolError: peer closed connection "
        "without sending complete message body (incomplete chunked read)",
    )
    assert is_cursor_bridge_stream_drop(exc)


def test_is_cursor_bridge_stream_drop_wrapped_remote_protocol() -> None:
    class RemoteProtocolError(Exception):
        pass

    inner = RemoteProtocolError(
        "peer closed connection without sending complete message body",
    )
    outer = NetworkError(f"Bridge request failed: {inner}")
    outer.__cause__ = inner
    assert is_cursor_bridge_stream_drop(outer)


def test_is_cursor_bridge_stream_drop_ignores_value_error() -> None:
    assert not is_cursor_bridge_stream_drop(ValueError("bad prompt"))


def test_is_cursor_provider_access_denied_region() -> None:
    class PermissionDeniedError(Exception):
        pass

    exc = PermissionDeniedError("permission_denied: any reason")
    assert is_cursor_provider_access_denied(exc)


def test_is_cursor_provider_access_denied_ignores_other() -> None:
    assert not is_cursor_provider_access_denied(ValueError("nope"))


def test_log_cursor_bridge_stream_drop_includes_cause_and_fileline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class RemoteProtocolError(Exception):
        pass

    inner = RemoteProtocolError("incomplete chunked read")
    exc = NetworkError("Bridge request failed: peer closed connection")
    exc.__cause__ = inner
    with caplog.at_level("ERROR", logger="agents.abc_agent"):
        log_cursor_bridge_stream_drop(
            agent_name="impl_verify round=1",
            run_id="run-abc",
            agent_id="agent-xyz",
            exc=exc,
            resume_attempt=1,
            max_resumes=2,
        )
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "ERROR"
    assert "abc_agent.py" in record.pathname or record.filename == "abc_agent.py"
    assert "cursor bridge stream drop" in record.message
    assert "run-abc" in record.message
    assert "agent-xyz" in record.message
    assert "NetworkError" in record.message
    assert "incomplete chunked read" in record.message
    assert record.exc_info is not None


def test_log_cursor_stream_stalled_uses_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from agents.adapters.cursor_sdk.cursor_sdk_stream import log_cursor_stream_stalled_on_unknown

    live_line = "12:00:00 [hop][agent] stream_unknown offset='1' wire='heartbeat'"
    with caplog.at_level("ERROR", logger="agents.adapters.cursor_sdk.cursor_sdk_stream"):
        log_cursor_stream_stalled_on_unknown(
            hop_label="hop|agent",
            run_id="run-1",
            agent_id="agent-1",
            consecutive_unknown=3,
            last_live_line=live_line,
        )
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "ERROR"
    assert "cursor stream stalled" in record.message
    assert live_line in record.message


def test_stream_sink_logs_via_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from agents.adapters.cursor_sdk.cursor_sdk_stream import CursorRunStreamSink

    sink = CursorRunStreamSink(
        label="verify|impl_verify round=1",
        ndjson_path=None,
        emit_stderr=True,
        run_id="run-1",
        agent_id="agent-1",
    )
    with caplog.at_level(logging.DEBUG, logger="agents.adapters.cursor_sdk.cursor_sdk_stream"):
        sink.emit(
            {
                "kind": "thinking",
                "text": "plan step one",
            }
        )
    assert any("thinking: plan step one" in r.message for r in caplog.records)
    assert all(r.levelname == "DEBUG" for r in caplog.records)


def test_stream_sink_stream_unknown_logs_diagnostic_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``stream_unknown`` — диагностика DEBUG; stall ERROR — отдельный путь."""
    import logging

    from agents.adapters.cursor_sdk.cursor_sdk_stream import CursorRunStreamSink

    sink = CursorRunStreamSink(
        label="hop|agent",
        ndjson_path=None,
        emit_stderr=True,
        run_id="run-1",
        agent_id="agent-1",
    )
    with caplog.at_level(logging.DEBUG, logger="agents.adapters.cursor_sdk.cursor_sdk_stream"):
        sink.emit(
            {
                "kind": "stream_unknown",
                "offset": "off-1",
                "wire_discriminator": "heartbeat",
                "wire_body": '{"heartbeat": {}}',
                "run_id": "run-1",
                "agent_id": "agent-1",
            }
        )
    assert caplog.records
    assert caplog.records[-1].levelname == "DEBUG"
    assert "stream_unknown" in caplog.records[-1].message
    assert "wire_body=" in caplog.records[-1].message


def test_format_live_line_preserves_full_user_json() -> None:
    from agents.adapters.cursor_sdk.cursor_sdk_stream import format_live_line

    long_prompt = "x" * 2000
    record = {"kind": "user", "payload": {"text": long_prompt}}
    line = format_live_line("hop|agent", record)
    assert long_prompt in line


def test_is_cursor_resume_target_gone_run_bad_request() -> None:
    exc = BadRequestError(
        "invalid_argument: Run run-dead not found.",
    )
    assert is_cursor_resume_target_gone(exc)


def test_is_cursor_resume_target_gone_ignores_unrelated_bad_request() -> None:
    exc = BadRequestError(
        "invalid_argument: Unknown tool name(s) in `tools`: write.",
    )
    assert not is_cursor_resume_target_gone(exc)


def test_is_cursor_resume_target_gone_checks_cause() -> None:
    inner = BadRequestError("invalid_argument: Run run-x not found.")
    outer = RuntimeError("resume failed")
    outer.__cause__ = inner
    assert is_cursor_resume_target_gone(outer)


@pytest.mark.asyncio
async def test_run_turn_stream_drop_then_run_not_found_fresh_launch(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NetworkError на wait → get_run not found → fresh create+send → успех."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    wait_calls = 0
    get_run_calls = 0
    create_calls = 0
    resume_calls = 0
    send_calls = 0

    async def fake_wait(*_args: Any, **_kwargs: Any) -> RunResult:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            raise NetworkError(
                "Bridge request failed: RemoteProtocolError: incomplete chunked read",
            )
        return RunResult(
            id="run-2",
            agent_id="agent-2",
            status="finished",
            result='{"ok": true}',
        )

    class FakeAgent:
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id

        async def send(self, *_args: Any, **_kwargs: Any) -> MagicMock:
            nonlocal send_calls
            send_calls += 1
            run = MagicMock()
            run.id = "run-1" if send_calls == 1 else "run-2"
            run.usage = None
            return run

        async def get_usage(self) -> MagicMock:
            billed = MagicMock()
            billed.usage = None
            billed.cost = None
            billed.runs = []
            return billed

    class FakeClient:
        async def get_run(self, run_id: str) -> MagicMock:
            nonlocal get_run_calls
            get_run_calls += 1
            raise BadRequestError(f"invalid_argument: Run {run_id} not found.")

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    async def fake_launch_bridge(**_kwargs: Any) -> FakeClient:
        return FakeClient()

    async def fake_create(_options: Any, *, client: Any) -> FakeAgent:
        nonlocal create_calls
        create_calls += 1
        assert client is not None
        return FakeAgent(f"agent-{create_calls}")

    async def fake_resume(agent_id: str, _options: Any, *, client: Any) -> FakeAgent:
        nonlocal resume_calls
        resume_calls += 1
        assert agent_id == "agent-1"
        assert client is not None
        return FakeAgent(agent_id)

    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.wait_cursor_run_with_live_log",
        fake_wait,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.AsyncClient.launch_bridge",
        fake_launch_bridge,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.AsyncAgent.create",
        fake_create,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.AsyncAgent.resume",
        fake_resume,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.resolve_auth",
        lambda: MagicMock(api_key="test-key"),
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.persist_run_dialog_artifacts",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.normalize_workspace_path",
        lambda workspace, repository_path: Path(str(workspace)),
    )

    adapter = CursorSDKAdapter(timeout_seconds=5)
    state = {
        "workspace": str(workspace),
        "repository_path": str(workspace),
        "model": {"id": "composer-2.5", "params": []},
        "stream_label": "test-hop",
    }
    with patch.object(ABCAgent, "cursor_stream_resume_max_attempts", 2):
        result = await adapter._run_turn(
            state,
            prompt="hello",
            tools=("read",),
            payload_model=None,
            resume_agent_id=None,
        )

    assert wait_calls == 2
    assert get_run_calls == 1
    assert create_calls == 2
    assert resume_calls == 1
    assert send_calls == 2
    assert result.run_id == "run-2"
    assert result.status == "finished"


@pytest.mark.asyncio
async def test_run_turn_one_stream_resume_then_success(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NetworkError на wait → один resume (AsyncAgent.resume + get_run) → успех."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    wait_calls = 0
    resume_calls = 0

    async def fake_wait(*_args: Any, **_kwargs: Any) -> RunResult:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            raise NetworkError(
                "Bridge request failed: RemoteProtocolError: incomplete chunked read",
            )
        return RunResult(
            id="run-1",
            agent_id="agent-1",
            status="finished",
            result='{"ok": true}',
        )

    class FakeAgent:
        agent_id = "agent-1"

        async def send(self, *_args: Any, **_kwargs: Any) -> MagicMock:
            run = MagicMock()
            run.id = "run-1"
            run.usage = None
            return run

        async def get_usage(self) -> MagicMock:
            billed = MagicMock()
            billed.usage = None
            billed.cost = None
            billed.runs = []
            return billed

    class FakeClient:
        async def get_run(self, run_id: str) -> MagicMock:
            assert run_id == "run-1"
            run = MagicMock()
            run.id = run_id
            run.usage = None
            return run

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    async def fake_launch_bridge(**_kwargs: Any) -> FakeClient:
        return FakeClient()

    async def fake_resume(agent_id: str, _options: Any, *, client: Any) -> FakeAgent:
        nonlocal resume_calls
        resume_calls += 1
        assert agent_id == "agent-1"
        assert client is not None
        return FakeAgent()

    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.wait_cursor_run_with_live_log",
        fake_wait,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.AsyncClient.launch_bridge",
        fake_launch_bridge,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.AsyncAgent.create",
        AsyncMock(return_value=FakeAgent()),
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.AsyncAgent.resume",
        fake_resume,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.resolve_auth",
        lambda: MagicMock(api_key="test-key"),
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.persist_run_dialog_artifacts",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.normalize_workspace_path",
        lambda workspace, repository_path: Path(str(workspace)),
    )

    adapter = CursorSDKAdapter(timeout_seconds=5)
    state = {
        "workspace": str(workspace),
        "repository_path": str(workspace),
        "model": {"id": "composer-2.5", "params": []},
        "stream_label": "test-hop",
    }
    with patch.object(ABCAgent, "cursor_stream_resume_max_attempts", 2):
        result = await adapter._run_turn(
            state,
            prompt="hello",
            tools=("read",),
            payload_model=None,
            resume_agent_id=None,
        )

    assert wait_calls == 2
    assert resume_calls == 1
    assert result.run_id == "run-1"
    assert result.status == "finished"


@pytest.mark.asyncio
async def test_run_turn_stream_resume_cap_reraises(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """После исчерпания лимита resume пробрасывается последний NetworkError."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async def always_fail(*_args: Any, **_kwargs: Any) -> RunResult:
        raise NetworkError("Bridge request failed: incomplete chunked read")

    class FakeAgent:
        agent_id = "agent-1"

        async def send(self, *_args: Any, **_kwargs: Any) -> MagicMock:
            run = MagicMock()
            run.id = "run-1"
            return run

    class FakeClient:
        async def get_run(self, run_id: str) -> MagicMock:
            run = MagicMock()
            run.id = run_id
            return run

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

    async def fake_launch_bridge(**_kwargs: Any) -> FakeClient:
        return FakeClient()

    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.wait_cursor_run_with_live_log",
        always_fail,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.AsyncClient.launch_bridge",
        fake_launch_bridge,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.AsyncAgent.create",
        AsyncMock(return_value=FakeAgent()),
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.AsyncAgent.resume",
        AsyncMock(return_value=FakeAgent()),
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.resolve_auth",
        lambda: MagicMock(api_key="test-key"),
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_adapter.normalize_workspace_path",
        lambda workspace, repository_path: Path(str(workspace)),
    )

    adapter = CursorSDKAdapter(timeout_seconds=5)
    state = {
        "workspace": str(workspace),
        "repository_path": str(workspace),
        "model": {"id": "composer-2.5", "params": []},
        "stream_label": "test-hop",
    }
    with patch.object(ABCAgent, "cursor_stream_resume_max_attempts", 2):
        with pytest.raises(NetworkError, match="incomplete chunked"):
            _ = await adapter._run_turn(
                state,
                prompt="hello",
                tools=("read",),
                payload_model=None,
                resume_agent_id=None,
            )
