"""Runtime-агент Typing Repair для static workflow (TYPING после triage)."""

from __future__ import annotations

from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_when_same_finding,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.repair.helpers import MUTATING
from agents.state import GraphState
from agents.verify.typing_repair.input_pack import build_typing_repair_input_pack, pack_json
from agents.verify.typing_repair.prompt.get_prompt import get_prompt
from agents.verify.typing_repair.schemas import TypingRepairResult
from workflows.repair.schemas import FixEvidence
from workflows.verify.gate_repair.static_scope import gate_issue_in_product_static_scope


class TypingRepairAgent:
    """Исполнитель typing-fix для одного static finding (отдельно от bug-repair workflow)."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self.validation_max_attempts = 3

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Вызвать LLM (или stub) и положить ``verify_static_fix`` для commit hop."""
        item = state.get("dispatcher_current_item")
        if not isinstance(item, dict):
            raise ValueError("dispatcher_current_item missing for typing_repair")

        observation = str(item.get("observation") or "")
        source_tool = str(item.get("source_tool") or "")
        if source_tool in ("pyright", "mypy") and not gate_issue_in_product_static_scope(
            observation,
        ):
            finding_id = str(item.get("finding_id") or "")
            blocked = TypingRepairResult(
                finding_id=finding_id,
                status="BLOCKED",
                summary=(
                    "Finding вне product static scope (typing_repair только src/stroy); "
                    "пропуск без LLM."
                ),
                files_changed=[],
                notes=observation[:500] or None,
            )
            result_dict = blocked.model_dump(mode="json")
            return {
                "typing_repair_result": result_dict,
                "verify_finding_fix": None,
                "verify_finding_repair_outcome": {
                    "workflow": "typing_repair",
                    "result": result_dict,
                },
                "typing_repair_execution_status": "SKIPPED_OUT_OF_SCOPE",
            }

        triage_raw = state.get("static_triage_result")
        if not isinstance(triage_raw, dict):
            raise ValueError("static_triage_result missing for typing_repair")

        change_id = str(state.get("change_id") or "").strip()
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)

        package = build_typing_repair_input_pack(
            repository_path=repo,
            item=item,
            state=state,
            triage=triage_raw,
        )
        prompt = get_prompt(input_pack_json=pack_json(package))
        finding_id = str(item.get("finding_id") or "")

        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("typing_repair_stream_log") or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "typing_repair"),
            ),
            prompt=prompt,
            agent_key="typing_repair",
            tools=MUTATING,
            resume_agent_id=resume_when_same_finding(
                state,
                stored_agent_id=state.get("typing_repair_agent_id"),
                bound_run_uuid=state.get("typing_repair_session_run_uuid"),
                prior_result=state.get("typing_repair_result"),
                finding_id=finding_id,
                unit_id=str(item.get("unit_id") or item.get("repair_unit_id") or ""),
            ),
            payload_model=TypingRepairResult,
            format_instructions=schema_format_reminder(TypingRepairResult),
            max_validation_attempts=self.validation_max_attempts,
        )
        parsed: TypingRepairResult = outcome.parsed  # type: ignore[assignment]
        parsed = parsed.model_copy(
            update={"finding_id": str(item.get("finding_id") or parsed.finding_id)},
        )
        result_dict = parsed.model_dump(mode="json")
        fix: dict[str, Any] | None = None
        if parsed.status == "FIXED":
            fix = FixEvidence(
                status="FIXED",
                summary=parsed.summary,
                files_changed=list(parsed.files_changed),
                notes=parsed.notes,
            ).model_dump(mode="json")

        return {
            "typing_repair_result": result_dict,
            "verify_finding_fix": fix,
            "verify_finding_repair_outcome": {
                "workflow": "typing_repair",
                "result": result_dict,
            },
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="typing_repair_agent_id",
            ),
            "typing_repair_run_id": outcome.run_id,
            "typing_repair_execution_status": outcome.status,
            "typing_repair_usage": outcome.usage,
            "typing_repair_cost": outcome.cost,
            "typing_repair_cost_status": outcome.cost_status,
            "typing_repair_billing_error": outcome.billing_error,
            "typing_repair_usage_source": outcome.usage_source,
            "typing_repair_validation_attempts": outcome.validation_attempts,
        }
