"""Fake ABCAdapter для e2e wiring без Cursor bridge."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from pydantic import BaseModel

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.config import ReadScope
from agents.agent_tools.image.image_asset_agent import ImageAssetHopAnswer


class RecordingFakeAdapter(ABCAdapter):
    """Записывает hop metadata; возвращает structured JSON для image_asset."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._handlers: dict[str, Callable[..., AgentResult]] = {}

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: type[BaseModel] | None = None,
        resume_agent_id: str | None = None,
        tool_call_guard: Any | None = None,
        read_scope: ReadScope | None = None,
        agent_key: str | None = None,
    ) -> AgentResult:
        call = {
            "agent_key": agent_key,
            "resume_agent_id": resume_agent_id,
            "tools": list(tools),
            "has_parent_ctx": state.get("agent_tool_parent_ctx") is not None,
            "has_child_tools": state.get("image_child_custom_tools") is not None,
        }
        self.calls.append(call)
        handler = self._handlers.get(str(agent_key or ""))
        if handler is not None:
            result = handler(state, prompt=prompt, tools=tools, payload_model=payload_model)
            if inspect.iscoroutine(result):
                result = await result
            return result
        if payload_model is ImageAssetHopAnswer:
            parsed = ImageAssetHopAnswer(status="BLOCKED", notes="fake")
            return AgentResult(
                text=parsed.model_dump_json(),
                status="COMPLETED",
                agent_id="fake-child",
                run_id="run-child",
                parsed=parsed,
            )
        return AgentResult(
            text="{}",
            status="COMPLETED",
            agent_id="fake-parent",
            run_id="run-parent",
        )
