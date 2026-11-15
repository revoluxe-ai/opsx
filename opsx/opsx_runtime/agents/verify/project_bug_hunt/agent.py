"""ProjectBugHuntAgent: READ_ONLY project-tuned discovery для verify."""

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
from agents.state import GraphState
from agents.verify.project_bug_hunt.prompt.get_prompt import get_prompt
from agents.verify.project_bug_hunt.schemas import ProjectBugHuntAnswer

# READ_ONLY hop: только path-scoped read; без grep/glob/ls/shell (repo_root_scan).
_PROJECT_BUG_HUNT_TOOLS = ("read",)


class ProjectBugHuntAgent(ABCAgent):
    """Primary project bug discovery hop; repair исполняет bug-team downstream.

    Инструменты совпадают с промптом: только ``read`` по DERIVED_REFERENCED_PATHS;
    repo-wide search tools запрещены guard'ом ``repo_root_scan``.
    """

    async def run(self, state: GraphState) -> dict[str, Any]:
        prompt = get_prompt(state, ProjectBugHuntAnswer, "русский")

        key, cached = ReadOnlyReviewDurablePass.read_cached(
            state,
            role="project_bug",
            prompt=prompt,
            level=None,
            model=ProjectBugHuntAnswer,
            pass_verdict="HUNT_COMPLETE",
            clean=lambda item: item.status == "HUNT_COMPLETE"
            and not item.findings
            and item.confirmed_production_bug_count == 0,
        )
        if cached is not None:
            answer, provenance = cached
            return {
                "review": answer.model_dump(mode="json"),
                "project_bug_hunt_status": answer.status,
                **agent_session_patch(state, agent_id="", agent_id_key="agent_id"),
                **reused_review_transport_patch(),
                "check_receipt": provenance,
            }
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_label=compose_agent_stream_label(state, "project_bug_hunt"),
            ),
            prompt=prompt,
            agent_key="project_bug_hunt",
            tools=_PROJECT_BUG_HUNT_TOOLS,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=ProjectBugHuntAnswer,
            format_instructions=schema_format_reminder(ProjectBugHuntAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        validated = ProjectBugHuntAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        result = outcome
        next_key = ReadOnlyReviewDurablePass.receipt_key(
            state, role="project_bug", prompt=prompt, level=None,
        )
        saved = ReadOnlyReviewDurablePass.save_clean(
            next_key if key == next_key else None,
            answer=validated, pass_verdict="HUNT_COMPLETE",
            clean=validated.status == "HUNT_COMPLETE" and not validated.findings and validated.confirmed_production_bug_count == 0,
            outcome=outcome,
        )
        return {
            "review": validated.model_dump(mode="json"),
            "project_bug_hunt_status": validated.status,
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
