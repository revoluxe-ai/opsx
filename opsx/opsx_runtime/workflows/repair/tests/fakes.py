"""Test doubles for the repair workflow."""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Mapping, Sequence

from agents.adapters.abc_adapter import ABCAdapter, AgentResult


def response(_role: str, payload: dict[str, Any]) -> str:
    """Fake model answer: typed payload JSON only (no repair.agent.v1 envelope)."""
    return json.dumps(payload, ensure_ascii=False)


class FakeAdapter(ABCAdapter):
    def __init__(
        self,
        replies: Sequence[str],
        *,
        usage: dict[str, int | None] | None = None,
        sdk_status: str = "finished",
    ):
        self.replies = deque(replies)
        self.usage = usage or {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 10,
            "cache_write_tokens": 0,
            "reasoning_tokens": None,
            "total_tokens": 130,
        }
        self.sdk_status = sdk_status
        self.calls: list[dict[str, Any]] = []

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        self.calls.append(
            {
                "state": dict(state),
                "prompt": prompt,
                "tools": list(tools),
            }
        )
        if not self.replies:
            raise AssertionError("FakeAdapter has no response for this hop")
        reply = self.replies.popleft()
        return AgentResult(
            text=reply,
            status=self.sdk_status,
            agent_id=f"agent-{len(self.calls)}",
            run_id=f"run-{len(self.calls)}",
            usage=dict(self.usage) if self.usage is not None else None,
            cost=None,
            usage_source="fake",
            cost_status="unavailable",
            billing_error=None,
        )
