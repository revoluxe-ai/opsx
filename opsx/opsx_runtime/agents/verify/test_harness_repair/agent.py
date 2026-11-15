"""Runtime-агент восстановления pytest collection (BUILD axis)."""

from __future__ import annotations

import json
from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.repair.helpers import MUTATING

# Harness repair: scoped read + edit; без grep/glob/ls/shell (repo_root_scan).
_TEST_HARNESS_REPAIR_TOOLS = tuple(
    t for t in MUTATING if t not in ("grep", "glob", "ls", "shell")
)
from agents.state import GraphState
from agents.verify.test_harness_repair.input_pack import TestHarnessRepairInputPack
from agents.verify.test_harness_repair.prompt.get_prompt import get_prompt
from agents.verify.test_harness_repair.schemas import TestHarnessRepairResult
from workflows.repair.schemas import FixEvidence


class TestHarnessRepairAgent:
    """Исполнитель harness-fix для collect-loop ``pytest_execute``."""

    __test__ = False

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self.validation_max_attempts = 3

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Положить ``verify_finding_fix`` для commit или inline apply."""
        raw_pack = state.get("test_harness_repair_input_pack")
        if isinstance(raw_pack, dict):
            package = TestHarnessRepairInputPack.model_validate(raw_pack)
        else:
            item = state.get("dispatcher_current_item")
            if not isinstance(item, dict):
                raise ValueError("dispatcher_current_item missing for test_harness_repair")
            from agents.verify.pytest_run_health import CollectHealthResult

            collect = CollectHealthResult(
                ok=False,
                exit_code=int(item.get("collect_exit_code") or 1),
                command=[],
                log_tail=str(item.get("observation") or ""),
                duration_ms=0,
            )
            from agents.verify.test_harness_repair.input_pack import build_harness_repair_input_pack

            package = build_harness_repair_input_pack(
                repository_path=str(state["repository_path"]),
                item=item,
                state=state,
                collect=collect,
            )

        prompt = get_prompt(input_pack_json=package.model_dump_json())
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("stream_log") or "",
                stream_label=compose_agent_stream_label(state, "test_harness_repair"),
            ),
            prompt=prompt,
            agent_key="test_harness_repair",
            tools=_TEST_HARNESS_REPAIR_TOOLS,
            resume_agent_id=None,
            payload_model=TestHarnessRepairResult,
            format_instructions=schema_format_reminder(TestHarnessRepairResult),
            max_validation_attempts=self.validation_max_attempts,
        )
        parsed: TestHarnessRepairResult = outcome.parsed  # type: ignore[assignment]
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
            "test_harness_repair_result": result_dict,
            "verify_finding_fix": fix,
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="test_harness_repair_agent_id",
            ),
            "test_harness_repair_run_id": outcome.run_id,
            "test_harness_repair_execution_status": outcome.status,
        }
