"""Bugbot review agent. Knows nothing about the implementation of the SDK."""

from __future__ import annotations

from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.abc_agent import ABCAgent
from agents.verify.check_reuse import (
    ReadOnlyReviewDurablePass,
    reused_review_transport_patch,
)
from agents.state import BugBotAnswer, GraphState
from agents.verify.bugbot.prompt.get_prompt import get_prompt


def parse_review(text: str) -> BugBotAnswer:
    """Совместимость: парсинг через общий hop (legacy callers)."""
    from agents.utils.response import parse_agent_json

    return parse_agent_json(text, BugBotAnswer)


class BugBotAgent(ABCAgent):
    async def run(self, state: GraphState) -> dict[str, Any]:
        from workflows.verify.verify_check_level import read_verify_check_level
        prompt = get_prompt(state, BugBotAnswer, "русский")

        level = read_verify_check_level(state, "bugbot")
        key, cached = ReadOnlyReviewDurablePass.read_cached(
            state,
            role="bugbot",
            prompt=prompt,
            level=level,
            model=BugBotAnswer,
            pass_verdict="CLEAN",
            clean=lambda item: item.status == "CLEAN" and not item.findings,
        )
        if cached is not None:
            answer, provenance = cached
            return {
                "review": answer.model_dump(mode="json"),
                **agent_session_patch(state, agent_id="", agent_id_key="agent_id"),
                **reused_review_transport_patch(),
                "check_receipt": provenance,
            }
        # Read-only review: без shell (pytest/обход gates → repo_root_scan).
        tools = ("read", "grep", "glob", "ls")
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_label=compose_agent_stream_label(state, "bugbot"),
            ),
            prompt=prompt,
            agent_key="bugbot",
            tools=tools,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=BugBotAnswer,
            format_instructions=schema_format_reminder(BugBotAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        validated = BugBotAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        result = outcome
        next_key = ReadOnlyReviewDurablePass.receipt_key(
            state, role="bugbot", prompt=prompt, level=level,
        )
        saved = ReadOnlyReviewDurablePass.save_clean(
            next_key if key == next_key else None,
            answer=validated, pass_verdict="CLEAN",
            clean=validated.status == "CLEAN" and not validated.findings,
            outcome=outcome,
        )
        return {
            "review": validated.model_dump(mode="json"),
            **agent_session_patch(state, agent_id=result.agent_id, agent_id_key="agent_id"),
            "run_id": result.run_id,
            "execution_status": result.status,
            "usage": result.usage,
            "cost": result.cost,
            "cost_status": result.cost_status,
            "billing_error": result.billing_error,
            "usage_source": result.usage_source,
            "validation_attempts": outcome.validation_attempts,
            "check_receipt": {
                "source": "fresh_audit", "saved": saved,
                "current_execution_id": str(state.get("run_uuid") or ""),
            },
        }
