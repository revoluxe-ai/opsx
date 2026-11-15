"""Runtime-агент Static Triage: LOGIC vs TYPING для static workflow."""

from __future__ import annotations

from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_when_same_finding,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.state import GraphState
from agents.verify.static_triage.input_pack import (
    build_static_triage_input_pack,
    pack_json,
)
from agents.verify.static_triage.prompt.get_prompt import get_prompt
from agents.verify.static_triage.schemas import StaticTriageResult


class StaticTriageAgent:
    """Read-only triage одного static finding внутри dispatcher pass."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self.validation_max_attempts = 3

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Вызвать LLM (или stub) и вернуть patch + advance dispatcher queue."""
        item = state.get("dispatcher_current_item")
        if not isinstance(item, dict):
            raise ValueError("dispatcher_current_item missing for static_triage")

        change_id = str(state.get("change_id") or "").strip()
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)

        package = build_static_triage_input_pack(
            repository_path=repo,
            item=item,
            state=state,
        )
        prompt = get_prompt(input_pack_json=pack_json(package))
        finding_id = str(item.get("finding_id") or "")

        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("static_triage_stream_log") or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "static_triage"),
            ),
            prompt=prompt,
            agent_key="static_triage",
            tools=(),
            resume_agent_id=resume_when_same_finding(
                state,
                stored_agent_id=state.get("static_triage_agent_id"),
                bound_run_uuid=state.get("static_triage_session_run_uuid"),
                prior_result=state.get("static_triage_result"),
                finding_id=finding_id,
                unit_id=str(item.get("unit_id") or item.get("repair_unit_id") or ""),
            ),
            payload_model=StaticTriageResult,
            format_instructions=schema_format_reminder(StaticTriageResult),
            max_validation_attempts=self.validation_max_attempts,
        )
        parsed: StaticTriageResult = outcome.parsed  # type: ignore[assignment]
        parsed = parsed.model_copy(
            update={
                "finding_id": str(item.get("finding_id") or parsed.finding_id),
                "change_slice_status": package["change_slice_status"],
                "evidence_refs": parsed.evidence_refs.model_copy(
                    update={
                        "path": parsed.evidence_refs.path or item.get("path"),
                        "line": parsed.evidence_refs.line or item.get("line"),
                        "change_hunk_present": bool(package["change_hunk_present"]),
                    },
                ),
            },
        )
        result_dict = parsed.model_dump(mode="json")
        return {
            "verify_dispatch_path": "static",
            "static_triage_result": result_dict,
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="static_triage_agent_id",
            ),
            "static_triage_run_id": outcome.run_id,
            "static_triage_execution_status": outcome.status,
            "static_triage_usage": outcome.usage,
            "static_triage_cost": outcome.cost,
            "static_triage_cost_status": outcome.cost_status,
            "static_triage_billing_error": outcome.billing_error,
            "static_triage_usage_source": outcome.usage_source,
            "static_triage_validation_attempts": outcome.validation_attempts,
        }
