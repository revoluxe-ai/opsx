"""Runtime-агент Test Triage: LOGIC vs TEST для pytest workflow."""

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
from agents.verify.test_triage.input_pack import build_test_triage_input_pack, pack_json
from agents.verify.test_triage.prompt.get_prompt import get_prompt
from agents.verify.test_triage.schemas import TestTriageResult

# Классификатор: только scoped MCP read (без grep/glob/ls — иначе repo_root_scan).
_TEST_TRIAGE_READ_TOOLS = ("read",)


class TestTriageAgent:
    """Read-only triage одного pytest finding внутри dispatcher pass."""

    __test__ = False

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self.validation_max_attempts = 3

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Классифицировать finding и выставить ``verify_dispatch_path=test``."""
        item = state.get("dispatcher_current_item")
        if not isinstance(item, dict):
            raise ValueError("dispatcher_current_item missing for test_triage")

        change_id = str(state.get("change_id") or "").strip()
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)

        package = build_test_triage_input_pack(
            repository_path=repo,
            item=item,
            state=state,
        )
        prompt = get_prompt(input_pack_json=pack_json(package))
        finding_id = str(item.get("finding_id") or "")

        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("test_triage_stream_log") or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "test_triage"),
            ),
            prompt=prompt,
            agent_key="test_triage",
            tools=_TEST_TRIAGE_READ_TOOLS,
            resume_agent_id=resume_when_same_finding(
                state,
                stored_agent_id=state.get("test_triage_agent_id"),
                bound_run_uuid=state.get("test_triage_session_run_uuid"),
                prior_result=state.get("test_triage_result"),
                finding_id=finding_id,
                unit_id=str(item.get("unit_id") or item.get("repair_unit_id") or ""),
            ),
            payload_model=TestTriageResult,
            format_instructions=schema_format_reminder(TestTriageResult),
            max_validation_attempts=self.validation_max_attempts,
        )
        parsed: TestTriageResult = outcome.parsed  # type: ignore[assignment]
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
            "verify_dispatch_path": "test",
            "test_triage_result": result_dict,
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="test_triage_agent_id",
            ),
            "test_triage_run_id": outcome.run_id,
            "test_triage_execution_status": outcome.status,
            "test_triage_usage": outcome.usage,
            "test_triage_cost": outcome.cost,
            "test_triage_cost_status": outcome.cost_status,
            "test_triage_billing_error": outcome.billing_error,
            "test_triage_usage_source": outcome.usage_source,
            "test_triage_validation_attempts": outcome.validation_attempts,
        }
