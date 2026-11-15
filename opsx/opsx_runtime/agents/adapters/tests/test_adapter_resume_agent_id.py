"""Tests for explicit ``resume_agent_id`` on ``ABCAdapter.run``."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest
from pydantic import BaseModel, ConfigDict

from agents.adapters.abc_adapter import (
    ABCAdapter,
    AgentResult,
    normalize_resume_agent_id,
    resolve_bound_resume_agent_id,
    resume_for_role,
    resume_when_same_finding,)

from agents.adapters.adapter_transport import adapter_transport_state

class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True


class _ProbeAdapter(ABCAdapter):
    def __init__(self) -> None:
        self.calls: list[str | None] = []

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
        _ = state, prompt, tools, payload_model, read_scope
        self.calls.append(resume_agent_id)
        agent_id = resume_agent_id or "agent-new"
        return AgentResult(
            text='{"ok": true}',
            status="finished",
            agent_id=agent_id,
            run_id="run-1",
        )


def test_adapter_transport_state_omits_graph_agent_id() -> None:
    transport = adapter_transport_state(
        {
            "repository_path": "/r",
            "workspace": "/r",
            "model": {"id": "x"},
            "agent_id": "must-not-leak",
            "group_error_agent_id": "also-no",
        },
        stream_label="hop",
    )
    assert "agent_id" not in transport


def test_normalize_resume_agent_id() -> None:
    assert normalize_resume_agent_id(None) is None
    assert normalize_resume_agent_id("") is None
    assert normalize_resume_agent_id("  agent-1  ") == "agent-1"


@pytest.mark.asyncio
async def test_run_passes_resume_to_adapter() -> None:
    adapter = _ProbeAdapter()
    _ = await adapter.run(
        adapter_transport_state({"repository_path": "/r", "model": {"id": "x"}}),
        prompt="p",
        tools=(),
        resume_agent_id="agent-resume",
        payload_model=_Answer,
        max_validation_attempts=1,
    )
    assert adapter.calls == ["agent-resume"]


@pytest.mark.asyncio
async def test_validation_retry_reuses_result_agent_id() -> None:
    class _FlakyAdapter(ABCAdapter):
        def __init__(self) -> None:
            self.n = 0
            self.calls: list[str | None] = []

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
            _ = state, prompt, tools, payload_model, read_scope
            self.calls.append(resume_agent_id)
            self.n += 1
            if self.n == 1:
                return AgentResult(
                    text='{"bad": true}',
                    status="finished",
                    agent_id="agent-session",
                    run_id="run-1",
                )
            return AgentResult(
                text='{"ok": true}',
                status="finished",
                agent_id="agent-session",
                run_id="run-2",
            )

    adapter = _FlakyAdapter()
    result = await adapter.run(
        adapter_transport_state({"repository_path": "/r", "model": {"id": "x"}}),
        prompt="p",
        tools=(),
        resume_agent_id=None,
        payload_model=_Answer,
        max_validation_attempts=3,
    )
    assert result.parsed is not None
    assert adapter.calls == [None, "agent-session"]


def test_resume_when_same_finding() -> None:
    state = {
        "run_uuid": "exec-a",
        "static_triage_agent_id": "agent-a",
        "static_triage_session_run_uuid": "exec-a",
        "static_triage_result": {"finding_id": "F-1"},
    }
    assert (
        resume_when_same_finding(
            state,
            stored_agent_id=state["static_triage_agent_id"],
            bound_run_uuid=state["static_triage_session_run_uuid"],
            prior_result=state["static_triage_result"],
            finding_id="F-1",
        )
        == "agent-a"
    )
    assert (
        resume_when_same_finding(
            state,
            stored_agent_id=state["static_triage_agent_id"],
            bound_run_uuid=state["static_triage_session_run_uuid"],
            prior_result=state["static_triage_result"],
            finding_id="F-2",
        )
        is None
    )


def test_resume_blocked_when_execution_mismatch() -> None:
    state = {
        "run_uuid": "exec-new",
        "group_error_agent_id": "agent-old",
        "group_error_session_run_uuid": "exec-old",
    }
    assert resume_for_role(state, "group_error_agent_id") is None
    assert (
        resolve_bound_resume_agent_id(
            state,
            stored_agent_id="agent-old",
            bound_run_uuid="exec-old",
        )
        is None
    )


def test_resume_when_same_finding_requires_matching_unit() -> None:
    state = {"run_uuid": "exec-a"}
    prior = {"finding_id": "F-1", "unit_id": "unit-a"}
    assert (
        resume_when_same_finding(
            state,
            stored_agent_id="agent-x",
            bound_run_uuid="exec-a",
            prior_result=prior,
            finding_id="F-1",
            unit_id="unit-b",
        )
        is None
    )
    assert (
        resume_when_same_finding(
            state,
            stored_agent_id="agent-x",
            bound_run_uuid="exec-a",
            prior_result=prior,
            finding_id="F-1",
            unit_id="unit-a",
        )
        == "agent-x"
    )


def test_roles_read_own_bound_fields_only() -> None:
    state = {
        "run_uuid": "exec-a",
        "agent_id": "impl-session",
        "session_run_uuid": "exec-a",
        "group_error_agent_id": "group-session",
        "group_error_session_run_uuid": "exec-a",
    }
    assert resume_for_role(state, "group_error_agent_id") == "group-session"
    assert resume_for_role(state, "diagnostic_agent_id") is None
    assert resume_for_role(state, "agent_id") == "impl-session"


@pytest.mark.asyncio
async def test_roles_use_own_resume_not_global() -> None:
    adapter = _ProbeAdapter()
    contaminated = {
        "repository_path": "/r",
        "model": {"id": "x"},
        "agent_id": "impl-verify-session",
        "group_error_agent_id": "group-session",
    }
    _ = await adapter.run(
        adapter_transport_state(contaminated),
        prompt="p",
        tools=(),
        resume_agent_id=normalize_resume_agent_id(
            contaminated.get("group_error_agent_id"),
        ),
        payload_model=_Answer,
        max_validation_attempts=1,
    )
    assert adapter.calls[-1] == "group-session"
