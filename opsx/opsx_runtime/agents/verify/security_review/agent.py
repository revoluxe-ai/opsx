"""SecurityReviewAgent: READ_ONLY security discovery для verify (/review-security)."""

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
    review_receipt_key,
    reused_review_transport_patch,
)
from agents.state import GraphState
from agents.verify.security_review.prompt.get_prompt import get_prompt
from agents.verify.security_review.schemas import SecurityReviewAnswer


class SecurityReviewAgent(ABCAgent):
    """Primary security discovery hop; repair исполняет bug-team downstream."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        from workflows.verify.verify_check_level import read_verify_check_level
        prompt = get_prompt(state, SecurityReviewAnswer, "русский")

        level = read_verify_check_level(state, "security")
        key, cached = ReadOnlyReviewDurablePass.read_cached(
            state,
            role="security",
            prompt=prompt,
            level=level,
            model=SecurityReviewAnswer,
            pass_verdict="SECURITY_COMPLETE",
            clean=lambda item: (
                item.status == "SECURITY_COMPLETE"
                and not item.findings
                and item.confirmed_security_issue_count == 0
            ),
        )
        if cached is not None:
            answer, provenance = cached
            return {
                "review": answer.model_dump(mode="json"),
                "security_review_status": answer.status,
                **agent_session_patch(state, agent_id="", agent_id_key="agent_id"),
                **reused_review_transport_patch(),
                "check_receipt": provenance,
            }
        # Read-only review: без shell (обход read-scope / повтор pytest).
        tools = ("read", "grep", "glob", "ls")
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_label=compose_agent_stream_label(state, "security_review"),
            ),
            prompt=prompt,
            agent_key="security_review",
            tools=tools,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=SecurityReviewAnswer,
            format_instructions=schema_format_reminder(SecurityReviewAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        validated = SecurityReviewAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        result = outcome
        next_key = review_receipt_key(
            state, role="security", prompt=prompt, level=read_verify_check_level(state, "security"),
        )
        saved = ReadOnlyReviewDurablePass.save_clean(
            next_key if key == next_key else None,
            answer=validated,
            pass_verdict="SECURITY_COMPLETE",
            clean=(
                validated.status == "SECURITY_COMPLETE"
                and not validated.findings
                and validated.confirmed_security_issue_count == 0
            ),
            outcome=outcome,
        )
        return {
            "review": validated.model_dump(mode="json"),
            "security_review_status": validated.status,
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
