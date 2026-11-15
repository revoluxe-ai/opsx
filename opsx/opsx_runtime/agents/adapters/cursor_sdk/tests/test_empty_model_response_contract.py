"""Terminal SDK status is not evidence of a nonempty, schema-valid answer."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import pytest
from pydantic import BaseModel, ConfigDict, StrictBool

from agents.adapters.abc_adapter import (
    ABCAdapter,
    AgentResult,
    AgentRunContractError,
)
from agents.adapters.cursor_sdk.cursor_sdk_adapter import (
    CursorSDKAdapter,
    _recover_empty_terminal_text,
    _recover_empty_terminal_conversation,
)
from agents.adapters.provider_run_types import RunResult


class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: StrictBool


class _SequenceAdapter(ABCAdapter):
    def __init__(self, answers: list[tuple[str, str]]) -> None:
        self.answers = answers
        self.prompts: list[str] = []
        self.resumes: list[str | None] = []

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: type[BaseModel] | None = None,
        resume_agent_id: str | None = None,
        read_scope: Any | None = None,
    ) -> AgentResult:
        _ = read_scope
        self.prompts.append(prompt)
        self.resumes.append(resume_agent_id)
        text, status = self.answers[len(self.prompts) - 1]
        return AgentResult(
            text=text, status=status, agent_id="agent-a",
            run_id=f"run-{len(self.prompts)}",
            usage={"input_tokens": 52576, "output_tokens": 853},
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", ["", " ", "\n\t"])
async def test_empty_finished_response_fails_closed_without_costly_llm_retry(empty, caplog):
    adapter = _SequenceAdapter([(empty, "finished"), ('{"ok":true}', "finished")])
    with pytest.raises(AgentRunContractError) as caught:
        await adapter.run(
            {"repository_path": "/fake"}, prompt="review product",
            tools=("read",), payload_model=_Answer, max_validation_attempts=4,
        )
    error = caught.value
    assert error.code == "EMPTY_MODEL_RESPONSE"
    assert error.run_id == "run-1"
    assert error.agent_id == "agent-a"
    assert error.attempt == 1
    assert len(adapter.prompts) == 1
    assert "EMPTY_MODEL_RESPONSE" in caplog.text
    assert "input_tokens" in caplog.text


@pytest.mark.asyncio
async def test_sdk_provider_error_retries_on_transient_provider_status():
    adapter = _SequenceAdapter([("", "error"), ('{"ok":true}', "finished")])
    answer = await adapter.run(
        {"repository_path": "/fake"},
        prompt="review",
        tools=(),
        payload_model=_Answer,
        max_validation_attempts=4,
    )
    assert answer.parsed is not None and answer.parsed.ok is True
    assert answer.validation_attempts == 2
    assert adapter.resumes == [None, "agent-a"]
    assert "PROVIDER_ERROR" in adapter.prompts[1]


@pytest.mark.asyncio
async def test_sdk_provider_error_exhausted_raises():
    adapter = _SequenceAdapter([("", "error"), ("", "error")])
    with pytest.raises(AgentRunContractError) as caught:
        await adapter.run(
            {"repository_path": "/fake"},
            prompt="review",
            tools=(),
            payload_model=_Answer,
            max_validation_attempts=2,
        )
    assert caught.value.code == "PROVIDER_ERROR"
    assert len(adapter.prompts) == 2


@pytest.mark.asyncio
async def test_malformed_nonempty_json_gets_schema_only_retry_in_same_agent(caplog):
    adapter = _SequenceAdapter([('{"ok":', "finished"), ('{"ok":true}', "finished")])
    answer = await adapter.run(
        {"repository_path": "/fake"}, prompt="review once",
        tools=("read",), payload_model=_Answer, max_validation_attempts=3,
    )
    assert answer.parsed is not None and answer.parsed.ok is True
    assert answer.validation_attempts == 2
    assert adapter.resumes == [None, "agent-a"]
    assert "Не проводи анализ файлов заново" in adapter.prompts[1]
    assert "JSON_PARSE_FAILED" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "code"),
    [('{"ok":', "JSON_PARSE_FAILED"), ('{"ok":"not-boolean"}', "SCHEMA_VALIDATION_FAILED")],
)
async def test_bounded_nonempty_validation_failure_has_stable_code_and_cause(response, code):
    adapter = _SequenceAdapter([(response, "finished")])
    with pytest.raises(AgentRunContractError) as caught:
        await adapter.run(
            {"repository_path": "/fake"}, prompt="review",
            tools=(), payload_model=_Answer, max_validation_attempts=1,
        )
    assert caught.value.code == code
    assert caught.value.run_id == "run-1"
    assert caught.value.__cause__ is not None
    assert len(adapter.prompts) == 1


class _GetRunClient:
    def __init__(self, fetched=None, error=None) -> None:
        self.fetched = fetched
        self.error = error
        self.calls = []

    async def get_run(self, run_id: str):
        self.calls.append(run_id)
        if self.error:
            raise self.error
        return self.fetched


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("id", "agent_id", "status", "body", "expected"),
    [
        ("run-1", "agent-1", "finished", '{"ok":true}', '{"ok":true}'),
        ("run-2", "agent-1", "finished", '{"ok":true}', None),
        ("run-1", "agent-wrong", "finished", '{"ok":true}', None),
        ("run-1", "agent-1", "running", '{"ok":true}', None),
        ("run-1", "agent-1", "finished", "", None),
    ],
)
async def test_getrun_recovery_is_bound_to_same_terminal_execution(
    id, agent_id, status, body, expected,
):
    client = _GetRunClient(
        SimpleNamespace(id=id, agent_id=agent_id, status=status, result=body)
    )
    if id != "run-1" or agent_id != "agent-1" or status != "finished":
        with pytest.raises(AgentRunContractError) as caught:
            await _recover_empty_terminal_text(
                client, run_id="run-1", agent_id="agent-1", status="finished",
            )
        assert caught.value.code == "SDK_RESULT_IDENTITY_CONFLICT"
    else:
        text = await _recover_empty_terminal_text(
            client, run_id="run-1", agent_id="agent-1", status="finished",
        )
        assert text == expected
    assert client.calls == ["run-1"]



def _single_run_conversation(answer: str, *, trailing_tool: bool = False) -> str:
    steps = [
        {"type": "thinkingMessage", "message": {"text": "Checking source"}},
        {"type": "assistantMessage", "message": {"text": "Starting verify"}},
        {"type": "assistantMessage", "message": {"text": answer}},
    ]
    if trailing_tool:
        steps.append({"type": "toolCall", "name": "read"})
    import json
    return json.dumps([
        {"type": "agentConversationTurn", "turn": {"steps": steps}},
    ])


class _ConversationRun:
    def __init__(self, body: str, *, run_id: str = "run-1", agent_id: str = "agent-1"):
        self.id, self.agent_id, self.body = run_id, agent_id, body
        self.calls = 0

    async def conversation_json(self):
        self.calls += 1
        return self.body


@pytest.mark.asyncio
async def test_empty_sdk_result_recovers_run_scoped_valid_final_json_from_transcript():
    # Real recorded runs have a single agentConversationTurn with earlier
    # progress messages followed by one complete assistant JSON message.
    run = _ConversationRun(_single_run_conversation('{"ok": true}'))
    recovered = await _recover_empty_terminal_conversation(
        run, run_id="run-1", agent_id="agent-1",
        status="finished", payload_model=_Answer,
    )
    assert recovered is not None
    assert _Answer.model_validate_json(recovered).ok is True
    assert run.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    "Running PHASE_IMPL verification: reading contract and inspecting implementation, tests,",
    '{"ok":',
    '{"ok": "not-boolean"}',
    "",
])
async def test_partial_or_invalid_final_conversation_never_authorizes_verify(answer):
    # The progress-only transcript is observed in the attached f977... run.
    run = _ConversationRun(_single_run_conversation(answer))
    assert await _recover_empty_terminal_conversation(
        run, run_id="run-1", agent_id="agent-1",
        status="finished", payload_model=_Answer,
    ) is None


@pytest.mark.asyncio
async def test_trailing_tool_call_or_multiturn_history_cannot_supply_final_json():
    import json
    valid = _single_run_conversation('{"ok": true}', trailing_tool=True)
    run = _ConversationRun(valid)
    assert await _recover_empty_terminal_conversation(
        run, run_id="run-1", agent_id="agent-1",
        status="finished", payload_model=_Answer,
    ) is None
    run.body = json.dumps(json.loads(_single_run_conversation('{"ok": true}')) * 2)
    assert await _recover_empty_terminal_conversation(
        run, run_id="run-1", agent_id="agent-1",
        status="finished", payload_model=_Answer,
    ) is None


@pytest.mark.asyncio
async def test_transcript_source_must_match_run_and_agent_identity():
    run = _ConversationRun(_single_run_conversation('{"ok": true}'))
    for run_id, agent_id, status in (
        ("run-other", "agent-1", "finished"),
        ("run-1", "agent-other", "finished"),
        ("run-1", "agent-1", "running"),
    ):
        assert await _recover_empty_terminal_conversation(
            run, run_id=run_id, agent_id=agent_id,
            status=status, payload_model=_Answer,
        ) is None
    assert run.calls == 0


@pytest.mark.asyncio
async def test_getrun_unavailable_does_not_raise_or_dispatch_model_again():
    client = _GetRunClient(error=RuntimeError("provider unavailable"))
    assert await _recover_empty_terminal_text(
        client, run_id="run-1", agent_id="agent-1", status="finished",
    ) is None
    assert client.calls == ["run-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fetched_text", "conversation_text", "expected_recovered"),
    [
        ('{"ok":true}', "", True),
        ("", _single_run_conversation('{"ok": true}'), True),
        ("", _single_run_conversation("Running PHASE_IMPL verification..."), False),
        ("", "", False),
    ],
)
async def test_sdk_adapter_reconciles_same_run_without_second_send(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fetched_text: str,
    conversation_text: str,
    expected_recovered: bool,
):
    from agents.adapters.cursor_sdk import cursor_sdk_adapter as sdk

    run_calls = {"send": 0, "get_run": 0, "conversation": 0}
    class _FakeAgent:
        agent_id = "agent-a"

        async def send(self, *_a, **_kw):
            run_calls["send"] += 1
            async def conversation_json():
                run_calls["conversation"] += 1
                return conversation_text
            return SimpleNamespace(
                id="run-1", agent_id="agent-a", usage=None,
                conversation_json=conversation_json,
            )

        async def get_usage(self):
            return SimpleNamespace(
                usage=None,
                cost=SimpleNamespace(raw_cost_cents=0, charged_cents=0),
                runs=[],
            )

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get_run(self, run_id: str):
            assert run_id == "run-1"
            run_calls["get_run"] += 1
            return SimpleNamespace(
                id=run_id, agent_id="agent-a",
                status="finished", result=fetched_text,
            )

    async def launch_bridge(**_):
        return _FakeClient()

    async def create_agent(*_args, **_kwargs):
        return _FakeAgent()

    async def fake_wait(*_args, **_kwargs):
        return RunResult(
            id="run-1", agent_id="agent-a", status="finished", result="",
        )

    @asynccontextmanager
    async def fake_capture(*_args, **_kwargs):
        yield None

    monkeypatch.setattr(sdk.AsyncClient, "launch_bridge", launch_bridge)
    monkeypatch.setattr(sdk.AsyncAgent, "create", create_agent)
    monkeypatch.setattr(sdk, "wait_cursor_run_with_live_log", fake_wait)
    monkeypatch.setattr(sdk, "adapter_inbox_capture_scope", fake_capture)
    monkeypatch.setattr(sdk, "persist_run_dialog_artifacts", lambda **_: None)
    monkeypatch.setattr(sdk, "resolve_auth", lambda: SimpleNamespace(api_key="fixture"))
    monkeypatch.setattr(sdk, "bridge_launch_kwargs", lambda **_: {})
    monkeypatch.setattr(sdk, "agent_local_options", lambda **_: {})
    monkeypatch.setattr(sdk, "cursor_sdk_builtin_tools_only_extras", lambda: {})

    # normalize_workspace_path requires the fixture to be an OPSX repo root.
    # This is test setup only: no production source or SDK behavior changes.
    (tmp_path / "opsx").mkdir()
    adapter = CursorSDKAdapter()
    state = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "model": {"id": "composer-2.5", "params": []},
    }
    if expected_recovered:
        reply = await adapter.run(
            state, prompt="review", tools=("read",),
            payload_model=_Answer,
        )
        assert reply.parsed is not None and reply.parsed.ok is True
    else:
        with pytest.raises(AgentRunContractError) as raised:
            await adapter.run(
                state, prompt="review", tools=("read",),
                payload_model=_Answer,
            )
        assert raised.value.code == "EMPTY_MODEL_RESPONSE"
    assert run_calls == {
        "send": 1,
        "get_run": 1,
        "conversation": 0 if fetched_text else 1,
    }
