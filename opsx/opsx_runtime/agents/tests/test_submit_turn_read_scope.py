"""Тесты ``ABCAgent.submit_turn`` (промпт scope + resume после нарушения)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest
from pydantic import BaseModel, ConfigDict

from agents.abc_agent import ABCAgent
from agents.adapters.abc_adapter import ABCAdapter, AgentResult, AgentRunContractError
from agents.config import GLOBAL_READ_SCOPE, ReadScopeViolation, resolve_read_scope
from agents.read_scope_turn import READ_SCOPE_MARKER, read_scope_correction_prompt


class _TinyAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool = True


class _ScopeRetryAdapter(ABCAdapter):
    def __init__(self) -> None:
        self.calls = 0
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
        tool_call_guard: Any | None = None,
        read_scope: Any | None = None,
    ) -> AgentResult:
        _ = state, payload_model, read_scope, tool_call_guard
        self.calls += 1
        self.prompts.append(prompt)
        self.resumes.append(resume_agent_id)
        if self.calls == 1:
            raise ReadScopeViolation(
                tool="glob",
                path="/repo",
                rule="repo_root_scan",
                agent_id="agent-scope-1",
                run_id="run-1",
            )
        return AgentResult(
            text='{"ok": true}',
            status="finished",
            agent_id="agent-scope-1",
            run_id="run-2",
        )


@pytest.mark.asyncio
async def test_submit_turn_appends_read_scope_marker() -> None:
    adapter = _ScopeRetryAdapter()
    scope = resolve_read_scope("apply_scaffold")
    state = {
        "repository_path": "C:/Users/vkita/my_code/opsx-implement",
        "change_id": "visual",
        "implementation_slice_id": "SLICE-005",
        "model": {"id": "x"},
    }
    _ = await ABCAgent.submit_turn(
        adapter,
        state,
        prompt="do work",
        tools=("read", "grep"),
        read_scope=scope,
        resume_agent_id=None,
    )
    assert READ_SCOPE_MARKER in adapter.prompts[0]
    assert "встроен" in adapter.prompts[0].lower()
    assert "REPO_ROOT" in adapter.prompts[0]
    assert "opsx-implement" in adapter.prompts[0]


def test_correction_prompt_slice_apply_forbids_opsx_tools() -> None:
    scope = resolve_read_scope("apply_green")
    state = {
        "change_id": "visual",
        "implementation_slice_id": "SLICE-005",
        "implementation_stage": "APPLY_GREEN",
    }
    text = read_scope_correction_prompt(
        ReadScopeViolation(
            tool="grep",
            path="C:/repo/opsx_tools",
            rule="outside_restrict_allow",
        ),
        scope,
        state,
    )
    assert "opsx_tools" in text
    assert "runtime" in text.lower()
    assert "apply-scaffold" not in text
    assert "встроен" in text.lower() or "исходном" in text.lower()
    assert "repo_root_scan" in text or "корня workspace" in text


@pytest.mark.asyncio
async def test_submit_turn_fresh_hop_after_scope_violation() -> None:
    adapter = _ScopeRetryAdapter()
    scope = resolve_read_scope("apply_scaffold")
    state = {
        "repository_path": "C:/Users/vkita/my_code/opsx-implement",
        "change_id": "visual",
        "model": {"id": "x"},
    }
    result = await ABCAgent.submit_turn(
        adapter,
        state,
        prompt="do work",
        tools=("glob",),
        read_scope=scope,
        resume_agent_id=None,
    )
    assert result.text == '{"ok": true}'
    assert adapter.calls == 2
    assert adapter.resumes == [None, None]
    assert "repo_root_scan" in adapter.prompts[1]
    assert "do work" in adapter.prompts[1]


class _AlwaysViolateAdapter(ABCAdapter):
    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: type[BaseModel] | None = None,
        resume_agent_id: str | None = None,
        tool_call_guard: Any | None = None,
        read_scope: Any | None = None,
    ) -> AgentResult:
        _ = state, prompt, tools, payload_model, resume_agent_id, tool_call_guard, read_scope
        raise ReadScopeViolation(
            tool="glob",
            path="/repo",
            rule="repo_root_scan",
            agent_id="agent-scope-1",
            run_id="run-x",
        )


@pytest.mark.asyncio
async def test_submit_turn_exhausted_scope_corrections() -> None:
    adapter = _AlwaysViolateAdapter()
    with pytest.raises(AgentRunContractError) as err:
        await ABCAgent.submit_turn(
            adapter,
            {"repository_path": "C:/Users/vkita/my_code/opsx-implement", "model": {"id": "x"}},
            prompt="x",
            tools=("read", "grep"),
            read_scope=GLOBAL_READ_SCOPE,
            resume_agent_id=None,
        )
    assert err.value.code == "READ_SCOPE_EXHAUSTED"


@pytest.mark.asyncio
async def test_submit_turn_scope_violation_without_discovery_does_not_resume() -> None:
    adapter = _AlwaysViolateAdapter()
    with pytest.raises(AgentRunContractError) as err:
        await ABCAgent.submit_turn(
            adapter,
            {"repository_path": "C:/Users/vkita/my_code/opsx-implement", "model": {"id": "x"}},
            prompt="x",
            tools=("read", "edit"),
            read_scope=GLOBAL_READ_SCOPE,
            resume_agent_id=None,
        )
    assert err.value.code == "TOOL_SCOPE_VIOLATION"
    assert err.value.attempt == 1


class _OutsideAllowThenOkAdapter(ABCAdapter):
    """Первый вызов: outside_restrict_allow на edit; второй — успех."""

    def __init__(self) -> None:
        self.calls = 0
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
        tool_call_guard: Any | None = None,
        read_scope: Any | None = None,
    ) -> AgentResult:
        _ = state, tools, payload_model, read_scope, tool_call_guard
        self.calls += 1
        self.prompts.append(prompt)
        self.resumes.append(resume_agent_id)
        if self.calls == 1:
            raise ReadScopeViolation(
                tool="edit",
                path="C:/Users/vkita/my_code/opsx-implement/tools/resize_visual_cover_pngs.py",
                rule="outside_restrict_allow",
                agent_id="agent-apply-1",
                run_id="run-1",
            )
        return AgentResult(
            text='{"ok": true}',
            status="finished",
            agent_id="agent-apply-1",
            run_id="run-2",
        )


class _AlwaysOutsideAllowAdapter(ABCAdapter):
    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: type[BaseModel] | None = None,
        resume_agent_id: str | None = None,
        tool_call_guard: Any | None = None,
        read_scope: Any | None = None,
    ) -> AgentResult:
        _ = state, prompt, tools, payload_model, resume_agent_id, tool_call_guard, read_scope
        raise ReadScopeViolation(
            tool="edit",
            path="C:/Users/vkita/my_code/opsx-implement/tools/resize_visual_cover_pngs.py",
            rule="outside_restrict_allow",
            agent_id="agent-apply-1",
            run_id="run-x",
        )


@pytest.mark.asyncio
async def test_non_discovery_outside_allow_one_correction_then_ok() -> None:
    adapter = _OutsideAllowThenOkAdapter()
    scope = resolve_read_scope("apply_implement")
    state = {
        "repository_path": "C:/Users/vkita/my_code/opsx-implement",
        "change_id": "visual",
        "implementation_slice_id": "SLICE-001",
        "model": {"id": "x"},
    }
    result = await ABCAgent.submit_turn(
        adapter,
        state,
        prompt="apply slice",
        tools=("read", "edit"),
        read_scope=scope,
        resume_agent_id=None,
        agent_key="apply_implement",
    )
    assert result.text == '{"ok": true}'
    assert adapter.calls == 2
    assert adapter.resumes == [None, None]
    assert "outside_restrict_allow" in adapter.prompts[1]
    assert "resize_visual_cover_pngs.py" in adapter.prompts[1]
    assert "BLOCKED" in adapter.prompts[1] or "remaining_gaps" in adapter.prompts[1]


@pytest.mark.asyncio
async def test_non_discovery_outside_allow_second_violation_cancels() -> None:
    adapter = _AlwaysOutsideAllowAdapter()
    scope = resolve_read_scope("apply_implement")
    with pytest.raises(AgentRunContractError) as err:
        await ABCAgent.submit_turn(
            adapter,
            {
                "repository_path": "C:/Users/vkita/my_code/opsx-implement",
                "change_id": "visual",
                "model": {"id": "x"},
            },
            prompt="apply slice",
            tools=("read", "edit"),
            read_scope=scope,
            resume_agent_id=None,
            agent_key="apply_implement",
        )
    assert err.value.code == "TOOL_SCOPE_VIOLATION"
    assert err.value.attempt == 2
