"""Unit tests for adapter live-log formatting and stream idle recovery."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agents.adapters.cursor_sdk.cursor_sdk_stream import (
    CURSOR_HOP_HARD_DEADLINE_SECONDS,
    CURSOR_STREAM_GETRUN_POLL_SECONDS,
    CURSOR_STREAM_GETRUN_RPC_TIMEOUT_SECONDS,
    CURSOR_STREAM_IDLE_TIMEOUT_SECONDS,
    CURSOR_STREAM_USEFUL_PROGRESS_STALL_SECONDS,
    _conversation_terminal_assistant_text,
    _maybe_report_consecutive_unknown_stall,
    compose_agent_stream_label,
    conversation_path_from_stream_log,
    format_live_line,
    log_cursor_hop_primary_exception,
    observe_task_read_scope_violation,
    useful_progress_stall_seconds_for_tools,
    wait_cursor_run_with_live_log,
)
from workflows.stop_errors import OpsxStageStop
from agents.config import GLOBAL_READ_SCOPE, ReadScopeViolation
from agents.read_scope_turn import build_tool_call_guard
from agents.abc_agent import ABCAgent
from cursor_sdk.types import RunStreamEvent as SdkRunStreamEvent

from agents.adapters.cursor_sdk import cursor_sdk_stream as cursor_sdk_stream_mod
from agents.adapters.provider_run_types import RunResult, RunStreamEvent

_LIVE_TS_PREFIX = re.compile(r"^\d{2}:\d{2}:\d{2} ")


def test_conversation_path_from_stream_log():
    p = Path("opsx/receipts/x/impl-verify-deadbeef.live.ndjson")
    assert conversation_path_from_stream_log(p) == p.with_name(
        "impl-verify-deadbeef.conversation.json"
    )
    assert ABCAgent.conversation_path_for_stream_log(p) == conversation_path_from_stream_log(
        p
    )


def test_format_thinking_line():
    record = {"kind": "thinking", "text": "plan A"}
    line = format_live_line("028-hop", record)
    assert _LIVE_TS_PREFIX.match(line)
    assert line.endswith("[028-hop] thinking: plan A")


def test_compose_agent_stream_label_includes_slice_for_split_apply_hops():
    """Split-apply SDK hops embed ``slice=`` для фильтрации live-log."""
    label = compose_agent_stream_label(
        {
            "model": {"id": "composer-2.5", "params": []},
            "stream_label": "verify-impl-visual",
            "implementation_slice_id": "SLICE-001",
        },
        "apply_scaffold",
    )
    assert label == "verify-impl-visual|apply_scaffold slice=SLICE-001 model=composer-2.5"


def test_compose_agent_stream_label_omits_slice_for_verify_agents():
    """Verify hops do not embed implementation slice id."""
    label = compose_agent_stream_label(
        {
            "model": {"id": "composer-2.5", "params": []},
            "stream_label": "verify-impl-visual",
            "implementation_slice_id": "SLICE-001",
        },
        "impl_verify",
    )
    assert label == "verify-impl-visual|impl_verify round=1 model=composer-2.5"
    assert "slice=" not in label


def test_format_thinking_line_with_agent_meta():
    record = {"kind": "thinking", "text": "read-only OPSX PHASE"}
    label = compose_agent_stream_label(
        {
            "model": {"id": "composer-2.5", "params": []},
            "verify_green_restart_count": 0,
            "stream_label": "verify-impl-invest-yalta-max-funnel",
        },
        "impl_verify",
    )
    assert (
        label
        == "verify-impl-invest-yalta-max-funnel|impl_verify round=1 model=composer-2.5"
    )
    line = format_live_line(label, record)
    assert _LIVE_TS_PREFIX.match(line)
    assert (
        "[verify-impl-invest-yalta-max-funnel][impl_verify round=1 model=composer-2.5] thinking:"
        in line
    )


def test_format_tool_call_line():
    record = {
        "kind": "tool_call",
        "name": "grep",
        "status": "running",
        "args": {"pattern": "foo"},
    }
    line = format_live_line("hop", record)
    assert _LIVE_TS_PREFIX.match(line)
    assert "tool grep" in line
    assert "running" in line


@dataclass
class _FakeFetchedRun:
    id: str
    agent_id: str
    status: str
    result: str = ""


class _FakeClient:
    def __init__(self, *, fetched: _FakeFetchedRun) -> None:
        self._fetched = fetched
        self.get_run_calls = 0
        self.cancel_run_calls = 0

    async def get_run(self, run_id: str) -> _FakeFetchedRun:
        self.get_run_calls += 1
        assert run_id == self._fetched.id
        return self._fetched

    async def cancel_run(self, run_id: str) -> None:
        self.cancel_run_calls += 1
        assert run_id == self._fetched.id

    async def wait_live_run(self, run_id: str) -> RunResult:
        assert run_id == self._fetched.id
        await asyncio.Event().wait()


class _MutableStatusClient(_FakeClient):
    """GetRun возвращает актуальный ``status``/``result`` (имитация tool-phase)."""

    def __init__(
        self,
        *,
        run_id: str,
        agent_id: str,
        initial_status: str,
        result: str = "",
    ) -> None:
        super().__init__(
            fetched=_FakeFetchedRun(
                id=run_id,
                agent_id=agent_id,
                status=initial_status,
                result=result,
            )
        )

    def set_status(self, status: str, *, result: str = "") -> None:
        self._fetched.status = status
        if result:
            self._fetched.result = result


class _RunningThenFinishedClient(_FakeClient):
    """Первые ``running_polls`` вызовов GetRun — ``running``, затем ``finished``."""

    def __init__(
        self,
        *,
        run_id: str,
        agent_id: str,
        running_polls: int,
        result: str,
    ) -> None:
        super().__init__(
            fetched=_FakeFetchedRun(
                id=run_id,
                agent_id=agent_id,
                status="running",
                result="",
            )
        )
        self._running_polls = running_polls
        self._terminal_result = result

    async def get_run(self, run_id: str) -> _FakeFetchedRun:
        self.get_run_calls += 1
        assert run_id == self._fetched.id
        if self._running_polls > 0:
            self._running_polls -= 1
            self._fetched.status = "running"
            return self._fetched
        self._fetched.status = "finished"
        self._fetched.result = self._terminal_result
        return self._fetched


class _FakeRun:
    def __init__(
        self,
        events: list[RunStreamEvent],
        *,
        client: _FakeClient,
        run_id: str = "run-test",
        agent_id: str = "agent-test",
    ) -> None:
        self.id = run_id
        self.agent_id = agent_id
        self.client = client
        self._events = list(events)
        self._terminal_result: RunResult | None = None
        self._turn_usages: list[Any] = []
        self._snapshot_usage = None

    def __aiter__(self) -> "_FakeRun":
        self._iter_index = 0
        return self

    async def __anext__(self) -> RunStreamEvent:
        if self._iter_index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._iter_index]
        self._iter_index += 1
        return event

    def _apply_result(self, result: RunResult) -> list[Any]:
        self._terminal_result = result
        return []

    def _result_with_accumulated_usage(self, result: RunResult) -> RunResult:
        return result

    async def wait(self) -> RunResult:
        if self._terminal_result is not None:
            return self._terminal_result
        return RunResult(
            id=self.id,
            agent_id=self.agent_id,
            status="finished",
            result="",
        )


class _BlockingFakeRun(_FakeRun):
    """Стрим без полезных событий: ``__anext__`` и ``wait()`` блокируются."""

    async def __anext__(self) -> RunStreamEvent:
        await asyncio.Event().wait()

    async def wait(self) -> RunResult:
        await asyncio.Event().wait()


class _ObserveFlakyRun(_BlockingFakeRun):
    """Первый ObserveRun падает; при живом GetRun=running observe переоткрывается."""

    def __init__(
        self,
        *,
        client: _FakeClient,
        run_id: str = "run-observe-flaky",
        agent_id: str = "agent-1",
    ) -> None:
        super().__init__([], client=client, run_id=run_id, agent_id=agent_id)
        self._observe_attempts = 0
        self._wait_started = asyncio.Event()
        self._finish_wait = asyncio.Event()
        self.mutating_send_calls = 0

    def send(self, *_args: Any, **_kwargs: Any) -> None:
        """Стрим recovery не должен инициировать повторный mutating send."""
        self.mutating_send_calls += 1
        raise AssertionError("unexpected mutating send during wait_cursor_run_with_live_log")

    def observe(self) -> Any:
        attempt = self._observe_attempts
        self._observe_attempts += 1

        async def _iter() -> Any:
            if attempt == 0:
                yield RunStreamEvent(kind="thinking", text="probe")
                raise RuntimeError("ObserveRun RPC dropped")
            await self._wait_started.wait()
            self._finish_wait.set()

        return _iter().__aiter__()

    async def wait(self) -> RunResult:
        self._wait_started.set()
        await asyncio.wait_for(self._finish_wait.wait(), timeout=2.0)
        return RunResult(
            id=self.id,
            agent_id=self.agent_id,
            status="finished",
            result="observe recovered",
        )


class _ObserveDoneEnvelopeRun(_BlockingFakeRun):
    """Observe шлёт ``done``/``result``, пока ``run.wait()`` ещё в работе."""

    def __init__(self, *, client: _FakeClient) -> None:
        super().__init__(
            [],
            client=client,
            run_id=client._fetched.id,
            agent_id=client._fetched.agent_id,
        )
        self._wait_started = asyncio.Event()
        self._release_wait = asyncio.Event()
        self.mutating_send_calls = 0

    def send(self, *_args: Any, **_kwargs: Any) -> None:
        self.mutating_send_calls += 1
        raise AssertionError("unexpected mutating send during observe-only recovery")

    def observe(self) -> Any:
        async def _iter() -> Any:
            yield RunStreamEvent(kind="done", done={"status": "finished"})
            yield RunStreamEvent(kind="result", result={"status": "finished"})
            await self._wait_started.wait()
            await self._release_wait.wait()

        return _iter().__aiter__()

    async def wait(self) -> RunResult:
        self._wait_started.set()
        await asyncio.wait_for(self._release_wait.wait(), timeout=2.0)
        return RunResult(
            id=self.id,
            agent_id=self.agent_id,
            status="finished",
            result="wait confirmed terminal",
        )

    def release_wait(self) -> None:
        self._release_wait.set()


class _TerminalAfterSilentObserve(_BlockingFakeRun):
    """Observe сразу EOF; терминал только через GetRun."""

    def __init__(self, *, client: _MutableStatusClient) -> None:
        super().__init__(
            [],
            client=client,
            run_id=client._fetched.id,
            agent_id=client._fetched.agent_id,
        )
        self._wait_release = asyncio.Event()

    def observe(self) -> Any:
        class _EmptyObserve:
            def __aiter__(self) -> "_EmptyObserve":
                return self

            async def __anext__(self) -> RunStreamEvent:
                raise StopAsyncIteration

        return _EmptyObserve()

    async def wait(self) -> RunResult:
        await self._wait_release.wait()
        return RunResult(
            id=self.id,
            agent_id=self.agent_id,
            status="finished",
            result="",
        )

    def release_wait(self) -> None:
        self._wait_release.set()


class _WaitWithTransport:
    """Имитация run с закрываемым transport при cancel ``wait()``."""

    def __init__(self) -> None:
        self.id = "run-transport"
        self.agent_id = "agent-transport"
        self.client = _FakeClient(
            fetched=_FakeFetchedRun(
                id="run-transport",
                agent_id="agent-transport",
                status="running",
            )
        )
        self._closed = False

    def observe(self) -> Any:
        async def _hang() -> Any:
            await asyncio.Event().wait()
            yield  # pragma: no cover

        return _hang().__aiter__()

    async def wait(self) -> RunResult:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self._closed = True
            raise
        return RunResult(
            id=self.id,
            agent_id=self.agent_id,
            status="finished",
            result="",
        )

    async def conversation_json(self) -> str:
        return ""


class _UnknownHeartbeatRun(_BlockingFakeRun):
    """Три подряд wire ``unknown`` (tool-gap heartbeat), затем блокировка."""

    def __init__(self, *, client: _FakeClient, unknown_count: int = 3) -> None:
        super().__init__(
            [],
            client=client,
            run_id=client._fetched.id,
            agent_id=client._fetched.agent_id,
        )
        self._unknown_count = unknown_count
        self._unknown_sent = 0
        self._wait_started = asyncio.Event()
        self._release_wait = asyncio.Event()

    def observe(self) -> Any:
        parent = self

        class _BurstObserve:
            def __aiter__(self) -> "_BurstObserve":
                return self

            async def __anext__(self) -> RunStreamEvent:
                if parent._unknown_sent < parent._unknown_count:
                    parent._unknown_sent += 1
                    await asyncio.sleep(0)
                    return RunStreamEvent(kind="unknown", offset=None)
                parent._wait_started.set()
                await parent._release_wait.wait()
                raise StopAsyncIteration

        return _BurstObserve()

    async def wait(self) -> RunResult:
        self._wait_started.set()
        await asyncio.wait_for(self._release_wait.wait(), timeout=2.0)
        return RunResult(
            id=self.id,
            agent_id=self.agent_id,
            status="finished",
            result="heartbeat burst ok",
        )

    def release_wait(self) -> None:
        self._release_wait.set()


class _ThinkingThenUnknownRun(_BlockingFakeRun):
    """Один useful thinking, затем wire heartbeat (классifier black hole)."""

    def __init__(self, *, client: _FakeClient) -> None:
        super().__init__(
            [],
            client=client,
            run_id=client._fetched.id,
            agent_id=client._fetched.agent_id,
        )
        self._thinking_sent = False

    def observe(self) -> Any:
        parent = self

        class _Observe:
            def __aiter__(self) -> "_Observe":
                return self

            async def __anext__(self) -> Any:
                if not parent._thinking_sent:
                    parent._thinking_sent = True
                    thinking = SimpleNamespace(type="thinking", text="user")
                    return SimpleNamespace(
                        kind="sdk",
                        sdk_message=thinking,
                        interaction_update=None,
                        step=None,
                        offset=None,
                    )
                await asyncio.sleep(0)
                return SimpleNamespace(
                    kind="unknown",
                    sdk_message=None,
                    interaction_update=None,
                    step=None,
                    offset=None,
                )

        return _Observe()

    async def wait(self) -> RunResult:
        await asyncio.Event().wait()
        return RunResult(
            id=self.id,
            agent_id=self.agent_id,
            status="finished",
            result="should not finish",
        )


class _OpenToolThenUnknownRun(_BlockingFakeRun):
    """In-flight tool call: stall guard не должен срабатывать."""

    def __init__(self, *, client: _FakeClient) -> None:
        super().__init__(
            [],
            client=client,
            run_id=client._fetched.id,
            agent_id=client._fetched.agent_id,
        )
        self._started = False

    def observe(self) -> Any:
        parent = self

        class _Observe:
            def __aiter__(self) -> "_Observe":
                return self

            async def __anext__(self) -> Any:
                if not parent._started:
                    parent._started = True
                    update = SimpleNamespace(
                        type="tool-call-started",
                        call_id="shell-1",
                        tool_call={"name": "shell"},
                    )
                    return SimpleNamespace(
                        kind="interaction",
                        sdk_message=None,
                        interaction_update=update,
                        step=None,
                        offset=None,
                    )
                await asyncio.sleep(0)
                return SimpleNamespace(
                    kind="unknown",
                    sdk_message=None,
                    interaction_update=None,
                    step=None,
                    offset=None,
                )

        return _Observe()

    async def wait(self) -> RunResult:
        await asyncio.Event().wait()
        return RunResult(
            id=self.id,
            agent_id=self.agent_id,
            status="finished",
            result="",
        )


@pytest.fixture
def clear_unknown_wire_diag_caches() -> Any:
    """Сброс module-level diag-кэшей между тестами ``from_json`` unknown wire."""
    cursor_sdk_stream_mod._STREAM_DEBUG_UNKNOWN_BY_OFFSET.clear()
    cursor_sdk_stream_mod._STREAM_UNKNOWN_DIAG_BY_OFFSET.clear()
    cursor_sdk_stream_mod._STREAM_DEBUG_UNKNOWN_TALLY.clear()
    yield
    cursor_sdk_stream_mod._STREAM_DEBUG_UNKNOWN_BY_OFFSET.clear()
    cursor_sdk_stream_mod._STREAM_UNKNOWN_DIAG_BY_OFFSET.clear()
    cursor_sdk_stream_mod._STREAM_DEBUG_UNKNOWN_TALLY.clear()


def test_run_stream_event_from_json_unknown_empty_heartbeat_stream_debug(
    monkeypatch: pytest.MonkeyPatch,
    clear_unknown_wire_diag_caches: Any,
) -> None:
    """Пустой wire heartbeat + ``OPSX_STREAM_DEBUG=1`` — без NameError (prod путь SDK)."""
    monkeypatch.setenv("OPSX_STREAM_DEBUG", "1")
    event = SdkRunStreamEvent.from_json({})
    assert event.kind == "unknown"
    assert cursor_sdk_stream_mod._unknown_diag_storage_key(event.offset) == "__none__"
    assert cursor_sdk_stream_mod._STREAM_UNKNOWN_DIAG_BY_OFFSET["__none__"][0] == "empty"
    assert cursor_sdk_stream_mod._STREAM_DEBUG_UNKNOWN_BY_OFFSET["__none__"] == "empty"


def test_run_stream_event_from_json_unknown_with_offset_stream_debug(
    monkeypatch: pytest.MonkeyPatch,
    clear_unknown_wire_diag_caches: Any,
) -> None:
    monkeypatch.setenv("OPSX_STREAM_DEBUG", "1")
    event = SdkRunStreamEvent.from_json({"offset": "wire-seq-7"})
    assert event.kind == "unknown"
    assert cursor_sdk_stream_mod._STREAM_UNKNOWN_DIAG_BY_OFFSET["wire-seq-7"][0] == "empty"
    assert cursor_sdk_stream_mod._STREAM_DEBUG_UNKNOWN_BY_OFFSET["wire-seq-7"] == "empty"


def test_run_stream_event_from_json_unknown_without_stream_debug(
    monkeypatch: pytest.MonkeyPatch,
    clear_unknown_wire_diag_caches: Any,
) -> None:
    monkeypatch.delenv("OPSX_STREAM_DEBUG", raising=False)
    event = SdkRunStreamEvent.from_json({})
    assert event.kind == "unknown"
    assert cursor_sdk_stream_mod._STREAM_UNKNOWN_DIAG_BY_OFFSET["__none__"][0] == "empty"
    assert cursor_sdk_stream_mod._STREAM_DEBUG_UNKNOWN_BY_OFFSET == {}


def test_record_event_unknown_none_offset_emits_empty_discriminator(
    monkeypatch: pytest.MonkeyPatch,
    clear_unknown_wire_diag_caches: Any,
    tmp_path: Path,
) -> None:
    """NDJSON ``stream_unknown`` с ``wire_discriminator='empty'`` после ``from_json``."""
    from agents.adapters.cursor_sdk.cursor_sdk_stream import CursorRunStreamSink

    monkeypatch.setenv("OPSX_STREAM_DEBUG", "1")
    log_path = tmp_path / "wire.live.ndjson"
    sink = CursorRunStreamSink(
        label="hop",
        ndjson_path=log_path,
        emit_stderr=False,
        run_id="run-1",
        agent_id="agent-1",
    )
    event = SdkRunStreamEvent.from_json({})
    sink.record_event(event)
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["kind"] == "stream_unknown"
    assert rows[-1]["wire_discriminator"] == "empty"
    assert rows[-1]["offset"] is None


class _ConversationCheckpointRun(_BlockingFakeRun):
    """GetRun=running, но ``conversation_json`` уже содержит финальный assistant."""

    def __init__(
        self,
        *,
        client: _FakeClient,
        conversation_payload: list[Any],
        run_id: str = "run-conv",
        agent_id: str = "agent-1",
    ) -> None:
        super().__init__([], client=client, run_id=run_id, agent_id=agent_id)
        self._conversation_raw = json.dumps(conversation_payload, ensure_ascii=False)

    async def conversation_json(self) -> str:
        return self._conversation_raw


@pytest.mark.asyncio
async def test_maybe_report_unknown_stall_skips_when_executor_running() -> None:
    """Три stream_unknown при GetRun=running — не stall ERROR (сброс счётчика)."""
    client = _MutableStatusClient(
        run_id="run-live-unknown",
        agent_id="agent-1",
        initial_status="running",
    )
    run = _BlockingFakeRun([], client=client, run_id=client._fetched.id)

    updated = await _maybe_report_consecutive_unknown_stall(
        run,
        hop_label="hop|agent",
        run_id=client._fetched.id,
        agent_id=client._fetched.agent_id,
        consecutive_unknown=3,
        last_unknown_live_line="wire heartbeat",
        observe_resume_count=0,
    )
    assert updated == 0


@pytest.mark.asyncio
async def test_maybe_report_unknown_stall_logs_when_executor_terminal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    client = _MutableStatusClient(
        run_id="run-dead-unknown",
        agent_id="agent-1",
        initial_status="finished",
        result="done",
    )
    run = _BlockingFakeRun([], client=client, run_id=client._fetched.id)

    with caplog.at_level(logging.ERROR, logger="agents.adapters.cursor_sdk.cursor_sdk_stream"):
        updated = await _maybe_report_consecutive_unknown_stall(
            run,
            hop_label="hop|agent",
            run_id=client._fetched.id,
            agent_id=client._fetched.agent_id,
            consecutive_unknown=3,
            last_unknown_live_line="wire heartbeat",
            observe_resume_count=1,
        )

    assert updated == 3
    assert any("cursor stream stalled" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_unknown_burst_during_live_run_does_not_log_stall(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Длинный tool-gap: heartbeat unknown не даёт false-positive stall при GetRun=running."""
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        60.0,
    )
    client = _MutableStatusClient(
        run_id="run-heartbeat",
        agent_id="agent-1",
        initial_status="running",
    )
    run = _UnknownHeartbeatRun(client=client, unknown_count=4)

    async def _finish_later() -> None:
        await asyncio.sleep(0.15)
        run.release_wait()

    asyncio.create_task(_finish_later())

    with caplog.at_level(logging.ERROR, logger="agents.adapters.cursor_sdk.cursor_sdk_stream"):
        result = await wait_cursor_run_with_live_log(
            run,
            timeout_seconds=2.0,
            label="test-unknown-burst",
            stream_log=None,
            emit_stderr=False,
        )

    assert result.result == "heartbeat burst ok"
    assert not any("cursor stream stalled" in r.message for r in caplog.records)


def test_stall_progress_ignores_thinking_only_sdk_message() -> None:
    from agents.adapters.cursor_sdk.cursor_sdk_stream import (
        _is_stall_progress_stream_event,
        _is_useful_stream_event,
    )

    class _Msg:
        type = "thinking"

    class _Evt:
        kind = "message"
        sdk_message = _Msg()
        interaction_update = None
        step = None

    evt = _Evt()
    assert _is_useful_stream_event(evt)  # type: ignore[arg-type]
    assert not _is_stall_progress_stream_event(evt)  # type: ignore[arg-type]


def test_live_stream_log_level_diag_kinds() -> None:
    from agents.adapters.agent_stream import live_stream_log_level
    import logging

    assert live_stream_log_level({"kind": "stream_unknown"}) == logging.DEBUG
    assert live_stream_log_level({"kind": "tool_call"}) == logging.INFO


def test_useful_progress_stall_for_tools_read_only_classifier():
    assert useful_progress_stall_seconds_for_tools(("read",)) == (
        CURSOR_STREAM_USEFUL_PROGRESS_STALL_SECONDS
    )
    assert useful_progress_stall_seconds_for_tools(("read", "edit")) is None
    assert useful_progress_stall_seconds_for_tools(("read", "run_pytest")) is None


@pytest.mark.asyncio
async def test_useful_progress_stall_cancels_hop_while_get_run_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thinking + heartbeat без прогресса → OpsxStageStop, не ожидание 600s idle."""
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        60.0,
    )
    client = _MutableStatusClient(
        run_id="run-stall",
        agent_id="agent-1",
        initial_status="running",
    )
    run = _ThinkingThenUnknownRun(client=client)

    with pytest.raises(OpsxStageStop) as err:
        await wait_cursor_run_with_live_log(
            run,
            timeout_seconds=5.0,
            label="003-classifier-test",
            stream_log=None,
            emit_stderr=False,
            useful_progress_stall_seconds=0.08,
        )

    assert err.value.code == "CURSOR_STREAM_USEFUL_PROGRESS_STALL"
    assert "run-stall" in err.value.evidence
    assert client.cancel_run_calls == 1


@pytest.mark.asyncio
async def test_useful_progress_stall_skips_while_tool_in_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        60.0,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_WAIT_STATE_INTERVAL_SECONDS",
        60.0,
    )
    client = _MutableStatusClient(
        run_id="run-tool-gap",
        agent_id="agent-1",
        initial_status="running",
    )
    run = _OpenToolThenUnknownRun(client=client)

    with pytest.raises(asyncio.TimeoutError):
        await wait_cursor_run_with_live_log(
            run,
            timeout_seconds=0.2,
            label="test-tool-gap",
            stream_log=None,
            emit_stderr=False,
            useful_progress_stall_seconds=0.05,
        )


@pytest.mark.asyncio
async def test_wait_state_emits_since_last_useful_seconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_WAIT_STATE_INTERVAL_SECONDS",
        0.02,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_GETRUN_POLL_SECONDS",
        0.02,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        600.0,
    )
    monkeypatch.setattr(
        "agents.adapters.adapter_stream_constants.CURSOR_STREAM_GETRUN_POLL_SECONDS",
        0.02,
    )
    client = _MutableStatusClient(
        run_id="run-wait-state",
        agent_id="agent-1",
        initial_status="running",
    )
    run = _ThinkingThenUnknownRun(client=client)
    log_path = tmp_path / "hop.live.ndjson"

    with pytest.raises(OpsxStageStop):
        await wait_cursor_run_with_live_log(
            run,
            timeout_seconds=2.0,
            label="wait-state-hop",
            stream_log=log_path,
            emit_stderr=False,
            useful_progress_stall_seconds=0.35,
        )

    wait_lines = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if '"kind": "wait_state"' in line
    ]
    assert wait_lines, "expected at least one wait_state ndjson row"
    row = wait_lines[0]
    assert "since_last_useful_seconds" in row
    assert row["since_last_useful_seconds"] >= row.get("useful_idle_seconds", 0) - 0.2


def test_conversation_terminal_assistant_text_requires_final_assistant():
    terminal = [
        {
            "type": "agentConversationTurn",
            "turn": {
                "steps": [
                    {"type": "toolCall", "message": {}},
                    {
                        "type": "assistantMessage",
                        "message": {"text": '{"verdict":"IMPL_GAPS"}'},
                    },
                ]
            },
        }
    ]
    assert _conversation_terminal_assistant_text(terminal) == '{"verdict":"IMPL_GAPS"}'
    in_progress = [
        {
            "type": "agentConversationTurn",
            "turn": {"steps": [{"type": "toolCall", "message": {}}]},
        }
    ]
    assert _conversation_terminal_assistant_text(in_progress) is None


@pytest.mark.asyncio
async def test_stream_idle_recovers_via_conversation_when_get_run_stuck_running(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        0.01,
    )
    fetched = _FakeFetchedRun(
        id="run-conv-recover",
        agent_id="agent-1",
        status="running",
    )
    client = _FakeClient(fetched=fetched)
    conversation = [
        {
            "type": "agentConversationTurn",
            "turn": {
                "steps": [
                    {
                        "type": "assistantMessage",
                        "message": {"text": '{"verdict":"IMPL_OK"}'},
                    }
                ]
            },
        }
    ]
    run = _ConversationCheckpointRun(
        client=client,
        conversation_payload=conversation,
        run_id=fetched.id,
        agent_id=fetched.agent_id,
    )

    result = await wait_cursor_run_with_live_log(
        run,
        timeout_seconds=2.0,
        label="test-conv-recover",
        stream_log=None,
        emit_stderr=False,
    )

    assert client.get_run_calls >= 1
    assert result.result == '{"verdict":"IMPL_OK"}'
    assert str(getattr(result.status, "value", result.status)) == "finished"


@pytest.mark.asyncio
async def test_stream_idle_recovers_finished_run_via_get_run(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        0.01,
    )

    fetched = _FakeFetchedRun(
        id="run-finished",
        agent_id="agent-1",
        status="finished",
        result="APPLY_PARTIAL",
    )
    client = _FakeClient(fetched=fetched)
    run = _BlockingFakeRun([], client=client, run_id=fetched.id, agent_id=fetched.agent_id)

    result = await wait_cursor_run_with_live_log(
        run,
        timeout_seconds=2700,
        label="test-idle-recover",
        stream_log=None,
        emit_stderr=False,
    )

    assert client.get_run_calls == 1
    assert result.result == "APPLY_PARTIAL"
    assert str(getattr(result.status, "value", result.status)) == "finished"


@pytest.mark.asyncio
async def test_stream_idle_raises_when_server_run_still_running(
    monkeypatch: pytest.MonkeyPatch,
):
    """Useful-idle → GetRun (диагностика); общий hop deadline → TimeoutError (не idle raise)."""
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        0.01,
    )

    fetched = _FakeFetchedRun(
        id="run-active",
        agent_id="agent-1",
        status="running",
    )
    client = _FakeClient(fetched=fetched)
    run = _BlockingFakeRun([], client=client, run_id=fetched.id)

    with pytest.raises(asyncio.TimeoutError):
        await wait_cursor_run_with_live_log(
            run,
            timeout_seconds=0.25,
            label="test-idle-active",
            stream_log=None,
            emit_stderr=False,
        )

    assert client.get_run_calls >= 1


def test_hop_hard_deadline_constant_matches_impl_verify_default():
    assert CURSOR_HOP_HARD_DEADLINE_SECONDS == 2700.0
    assert CURSOR_STREAM_IDLE_TIMEOUT_SECONDS == 600.0
    assert CURSOR_STREAM_USEFUL_PROGRESS_STALL_SECONDS == 300.0
    assert CURSOR_STREAM_GETRUN_POLL_SECONDS == 90.0
    assert CURSOR_STREAM_GETRUN_POLL_SECONDS <= CURSOR_STREAM_IDLE_TIMEOUT_SECONDS


def test_format_get_run_poll_line():
    line = format_live_line(
        "hop",
        {
            "kind": "get_run_poll",
            "status": "running",
            "observe_idle_seconds": 600,
        },
    )
    assert "get_run_poll" in line
    assert "running" in line


@pytest.mark.asyncio
async def test_stream_idle_emits_get_run_poll_when_still_running(
    monkeypatch: pytest.MonkeyPatch,
):
    """GetRun=running при useful-idle — hop живёт до общего deadline (без idle-cancel)."""
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_GETRUN_POLL_SECONDS",
        0.01,
    )

    client = _MutableStatusClient(
        run_id="run-active",
        agent_id="agent-1",
        initial_status="running",
    )
    run = _BlockingFakeRun([], client=client, run_id=client._fetched.id)

    with pytest.raises(asyncio.TimeoutError):
        await wait_cursor_run_with_live_log(
            run,
            timeout_seconds=0.2,
            label="test-get-run-poll",
            stream_log=None,
            emit_stderr=False,
        )

    assert client.get_run_calls >= 1


@pytest.mark.asyncio
async def test_stream_idle_recovers_when_get_run_finishes_on_later_poll(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        0.02,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_GETRUN_POLL_SECONDS",
        0.02,
    )

    client = _RunningThenFinishedClient(
        run_id="run-later-finish",
        agent_id="agent-1",
        running_polls=2,
        result="DONE_VIA_POLL",
    )
    run = _BlockingFakeRun([], client=client, run_id=client._fetched.id)

    result = await wait_cursor_run_with_live_log(
        run,
        timeout_seconds=2.0,
        label="test-later-finish",
        stream_log=None,
        emit_stderr=False,
    )

    assert client.get_run_calls >= 2
    assert result.result == "DONE_VIA_POLL"
    assert str(getattr(result.status, "value", result.status)) == "finished"


def test_conversation_rejects_thinking_message_as_terminal():
    payload = [
        {
            "type": "agentConversationTurn",
            "turn": {
                "steps": [
                    {
                        "type": "thinkingMessage",
                        "message": {"text": '{"answer":"OK"}'},
                    }
                ]
            },
        }
    ]
    assert _conversation_terminal_assistant_text(payload) is None


@pytest.mark.asyncio
async def test_observe_rpc_error_recovers_on_same_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        10.0,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_GETRUN_POLL_SECONDS",
        0.05,
    )
    fetched = _FakeFetchedRun(
        id="run-observe-flaky",
        agent_id="agent-1",
        status="running",
    )
    client = _FakeClient(fetched=fetched)
    run = _ObserveFlakyRun(client=client)

    result = await wait_cursor_run_with_live_log(
        run,
        timeout_seconds=3.0,
        label="test-observe-flaky",
        stream_log=None,
        emit_stderr=False,
    )

    assert run._observe_attempts >= 2
    assert result.result == "observe recovered"
    assert run.mutating_send_calls == 0


@pytest.mark.asyncio
async def test_observe_done_envelope_does_not_short_circuit_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal wire в observe не завершает hop без ``run.wait()`` / GetRun."""
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        10.0,
    )
    client = _MutableStatusClient(
        run_id="run-observe-done",
        agent_id="agent-1",
        initial_status="running",
    )
    run = _ObserveDoneEnvelopeRun(client=client)

    async def _finish_later() -> None:
        await asyncio.sleep(0.12)
        run.release_wait()

    asyncio.create_task(_finish_later())
    result = await wait_cursor_run_with_live_log(
        run,
        timeout_seconds=2.0,
        label="test-observe-done",
        stream_log=None,
        emit_stderr=False,
    )

    assert result.result == "wait confirmed terminal"
    assert run.mutating_send_calls == 0
    assert client.get_run_calls == 0


@pytest.mark.asyncio
async def test_observe_rpc_error_logs_resume_without_second_send(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Observe failure при живом run → observe_resume в live-log, без mutating send."""
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        10.0,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_GETRUN_POLL_SECONDS",
        0.05,
    )
    fetched = _FakeFetchedRun(
        id="run-observe-flaky",
        agent_id="agent-1",
        status="running",
    )
    client = _FakeClient(fetched=fetched)
    run = _ObserveFlakyRun(client=client)
    log_path = tmp_path / "observe-resume.live.ndjson"

    result = await wait_cursor_run_with_live_log(
        run,
        timeout_seconds=3.0,
        label="test-observe-resume-log",
        stream_log=log_path,
        emit_stderr=False,
    )

    assert result.result == "observe recovered"
    assert run.mutating_send_calls == 0
    ndjson = log_path.read_text(encoding="utf-8")
    assert '"kind": "observe_resume"' in ndjson
    assert run._observe_attempts >= 2


@pytest.mark.asyncio
async def test_observe_silent_then_getrun_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        0.02,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_GETRUN_POLL_SECONDS",
        0.02,
    )
    client = _MutableStatusClient(
        run_id="run-silent",
        agent_id="agent-1",
        initial_status="running",
    )
    run = _TerminalAfterSilentObserve(client=client)

    async def _finish_later() -> None:
        await asyncio.sleep(0.08)
        client.set_status("finished", result='{"ok":true}')
        run.release_wait()

    asyncio.create_task(_finish_later())
    result = await wait_cursor_run_with_live_log(
        run,
        timeout_seconds=2.0,
        label="test-silent-terminal",
        stream_log=None,
        emit_stderr=False,
    )

    assert str(getattr(result.status, "value", result.status)) == "finished"


@pytest.mark.asyncio
async def test_getrun_rpc_timeout_keeps_root_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_GETRUN_POLL_SECONDS",
        0.01,
    )
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_GETRUN_RPC_TIMEOUT_SECONDS",
        0.05,
    )

    class _SlowClient(_FakeClient):
        async def get_run(self, run_id: str) -> _FakeFetchedRun:
            await asyncio.sleep(0.2)
            return await super().get_run(run_id)

    client = _SlowClient(
        fetched=_FakeFetchedRun(
            id="run-slow-get",
            agent_id="agent-1",
            status="running",
        )
    )
    run = _BlockingFakeRun([], client=client, run_id="run-slow-get")

    with pytest.raises(asyncio.TimeoutError, match="exceeded 0.35s"):
        await wait_cursor_run_with_live_log(
            run,
            timeout_seconds=0.35,
            label="test-getrun-timeout",
            stream_log=None,
            emit_stderr=False,
        )
    assert client.get_run_calls >= 1


@pytest.mark.asyncio
async def test_cleanup_awaits_cancelled_wait_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        0.01,
    )
    run = _WaitWithTransport()
    with pytest.raises(asyncio.TimeoutError):
        await wait_cursor_run_with_live_log(
            run,
            timeout_seconds=0.08,
            label="test-cleanup",
            stream_log=None,
            emit_stderr=False,
        )
    assert run._closed


def test_log_cursor_hop_primary_exception_uses_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.ERROR):
        log_cursor_hop_primary_exception(
            phase="wait",
            rpc="run.wait",
            label="hop",
            run_id="run-1",
            agent_id="agent-1",
            elapsed_seconds=12.5,
            exc=RuntimeError("bridge down"),
        )
    assert any("cursor hop primary failure" in rec.message for rec in caplog.records)


def test_conversation_rejects_prose_impl_ok_marker():
    terminal = [
        {
            "type": "agentConversationTurn",
            "turn": {
                "steps": [
                    {
                        "type": "assistantMessage",
                        "message": {"text": "✅ IMPL_OK — всё готово"},
                    },
                ]
            },
        }
    ]
    assert _conversation_terminal_assistant_text(terminal) is None


@pytest.mark.asyncio
async def test_observe_task_read_scope_violation_helper() -> None:
    async def _fail() -> None:
        raise ReadScopeViolation(
            tool="grep",
            path="/",
            rule="repo_root_scan",
            agent_id="agent-x",
        )

    task = asyncio.create_task(_fail())
    with pytest.raises(ReadScopeViolation):
        await task
    found = observe_task_read_scope_violation(task)
    assert found is not None
    assert found.agent_id == "agent-x"


@pytest.mark.asyncio
async def test_wait_raises_read_scope_when_wait_cancelled_races_observe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scope violation из observe не маскируется под ``run.wait()=cancelled``."""
    from types import SimpleNamespace

    repo = Path("C:/Users/vkita/my_code/opsx-implement")
    guard = build_tool_call_guard(GLOBAL_READ_SCOPE, repo)
    fake_client = _FakeClient(
        fetched=_FakeFetchedRun(
            id="run-scope-race",
            agent_id="agent-scope",
            status="cancelled",
            result="",
        )
    )
    update = SimpleNamespace(
        type="tool-call-started",
        call_id="tc-1",
        tool_call={
            "name": "grep",
            "args": {"path": str(repo), "pattern": "x"},
        },
    )

    class _ScopeRaceRun:
        id = "run-scope-race"
        agent_id = "agent-scope"
        client = fake_client
        _yielded = False

        def observe(self) -> Any:
            outer = self

            class _Obs:
                def __aiter__(self) -> "_Obs":
                    return self

                async def __anext__(self) -> Any:
                    if outer._yielded:
                        raise StopAsyncIteration
                    outer._yielded = True
                    return SimpleNamespace(
                        kind="interaction",
                        interaction_update=update,
                        sdk_message=None,
                        step=None,
                    )

            return _Obs()

        async def wait(self) -> RunResult:
            await asyncio.sleep(0.05)
            return SimpleNamespace(
                id=self.id,
                agent_id=self.agent_id,
                status="cancelled",
                result="",
            )

    monkeypatch.setattr(
        "agents.adapters.cursor_sdk.cursor_sdk_stream.CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
        60.0,
    )
    with pytest.raises(ReadScopeViolation) as exc_info:
        await wait_cursor_run_with_live_log(
            _ScopeRaceRun(),
            timeout_seconds=5.0,
            label="test-scope-race",
            stream_log=None,
            emit_stderr=False,
            tool_call_guard=guard,
            repo_root=repo,
        )
    assert exc_info.value.rule == "repo_root_scan"


def test_read_scope_hop_finally_drains_cancelled_tasks() -> None:
    """При ``ReadScopeViolation`` finally ждёт drain observe/wait (bounded)."""
    import inspect

    from agents.adapters.cursor_sdk import cursor_sdk_stream as stream_mod

    src = inspect.getsource(stream_mod.wait_cursor_run_with_live_log)
    scope_finally = src.split("if scope_hop_exit:", 1)[1].split("else:", 1)[0]
    assert "_await_cancelled_tasks(observe_task, wait_task)" in scope_finally
    assert "CURSOR_HOP_CANCEL_DRAIN_SECONDS" in scope_finally


@pytest.mark.asyncio
async def test_aclose_observe_iterator_suppresses_teardown_runtime_error() -> None:
    """Штатный aclose не поднимает RuntimeError SDK async-generator teardown."""

    class _FakeObserveIter:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True
            raise RuntimeError("generator didn't stop after athrow()")

    fake = _FakeObserveIter()
    await cursor_sdk_stream_mod._aclose_observe_iterator(fake)  # type: ignore[arg-type]
    assert fake.closed
