"""Read-only агент test_interaction: классификация batch FAIL + single PASS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_when_same_finding,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.state import GraphState
from agents.verify.test_interaction.input_pack import build_test_interaction_input_pack, pack_json
from agents.verify.test_interaction.prompt.get_prompt import get_prompt
from agents.verify.test_interaction.schemas import TestInteractionResult
from workflows.verify.dispatch_cycle.pytest_interaction_repro import run_bounded_interaction_repro

_READ_TOOLS = ("read",)


class TestInteractionAgent:
    """Классифицирует interaction/flaky/order; не меняет код."""

    __test__ = False

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self.validation_max_attempts = 3

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Вызвать LLM (или adapter) и при необходимости bounded repro."""
        item = state.get("dispatcher_current_item")
        if not isinstance(item, dict):
            raise ValueError("dispatcher_current_item missing for test_interaction")

        repo = str(state["repository_path"])
        package = build_test_interaction_input_pack(
            repository_path=repo,
            item=item,
            state=state,
        )
        prompt = get_prompt(input_pack_json=pack_json(package))
        finding_id = str(item.get("finding_id") or "")

        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("test_interaction_stream_log") or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "test_interaction"),
            ),
            prompt=prompt,
            agent_key="test_interaction",
            tools=_READ_TOOLS,
            resume_agent_id=resume_when_same_finding(
                state,
                stored_agent_id=state.get("test_interaction_agent_id"),
                bound_run_uuid=state.get("test_interaction_session_run_uuid"),
                prior_result=state.get("test_interaction_result"),
                finding_id=finding_id,
                unit_id=str(item.get("unit_id") or item.get("repair_unit_id") or ""),
            ),
            payload_model=TestInteractionResult,
            format_instructions=schema_format_reminder(TestInteractionResult),
            max_validation_attempts=self.validation_max_attempts,
        )
        parsed: TestInteractionResult = outcome.parsed  # type: ignore[assignment]
        repro_evidence: dict[str, Any] | None = None
        if parsed.repro_request != "none":
            probe = state.get("pytest_single_repro_result")
            node_ids: list[str] = []
            if isinstance(probe, dict) and probe.get("node_id"):
                node_ids = [str(probe["node_id"])]
            repro_evidence = run_bounded_interaction_repro(
                Path(repo),
                node_ids=node_ids,
                kind=(
                    "concurrent"
                    if parsed.repro_request == "concurrent"
                    else "ordered_sequence"
                ),
                state=state,
            )
            if (
                parsed.classification == "RACE"
                and isinstance(repro_evidence, dict)
                and repro_evidence.get("status") == "UNCLEAR"
            ):
                parsed = parsed.model_copy(
                    update={
                        "classification": "UNCLEAR",
                        "repair_owner": "UNCLEAR",
                        "rationale": (
                            f"{parsed.rationale} "
                            "[repro: concurrency not established]"
                        ),
                    }
                )

        result_dict = parsed.model_dump(mode="json")
        return {
            "verify_dispatch_path": "test",
            "test_interaction_result": result_dict,
            "test_interaction_sequence_evidence": repro_evidence,
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="test_interaction_agent_id",
            ),
            "test_interaction_run_id": outcome.run_id,
            "test_interaction_execution_status": outcome.status,
            "test_interaction_usage": outcome.usage,
            "test_interaction_cost": outcome.cost,
            "test_interaction_cost_status": outcome.cost_status,
            "test_interaction_billing_error": outcome.billing_error,
            "test_interaction_usage_source": outcome.usage_source,
            "test_interaction_validation_attempts": outcome.validation_attempts,
        }
