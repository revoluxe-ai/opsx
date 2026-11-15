"""Агент длительности: timeout и stall, отдельно от test triage (LOGIC/TEST)."""

from __future__ import annotations

import json
from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_when_same_finding,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.state import GraphState
from agents.verify.dispatch_unit.schemas import CausalRepairGroup, RepairUnitTriageResult
from agents.verify.dispatch_unit.validation import validate_repair_unit_triage_partition
from agents.verify.test_duration.prompt.get_prompt import get_prompt
from agents.verify.test_duration.schemas import TestDurationResult
from agents.verify.test_duration.timeout_policy import deterministic_duration_classification
from agents.verify.test_triage.input_pack import build_test_triage_input_pack
from agents.verify.static_triage.schemas import StaticTriageEvidenceRef

_READ_TOOLS = ("read",)


class TestDurationAgent:
    """Read-only hop только для timeout/stall. Код не меняет."""

    __test__ = False

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter
        self.validation_max_attempts = 3

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Классифицировать зависание текущего pytest finding."""
        item = state.get("dispatcher_current_item")
        if not isinstance(item, dict):
            raise ValueError("dispatcher_current_item missing for test_duration")
        finding_item = _finding_item(item)
        finding_id = str(finding_item.get("finding_id") or "")
        observation = str(finding_item.get("observation") or "")
        node_id = str(
            finding_item.get("node_id")
            or finding_item.get("pytest_node_id")
            or "",
        )
        stall_evidence = finding_item.get("pytest_stall_evidence")
        if not isinstance(stall_evidence, dict):
            stall_evidence = None
        personal_timeout_probe = state.get("personal_timeout_probe")
        if not isinstance(personal_timeout_probe, dict):
            personal_timeout_probe = None
        det = deterministic_duration_classification(
            observation=observation,
            node_id=node_id,
            stall_evidence=stall_evidence,
            personal_timeout_probe=personal_timeout_probe,
            duration_probe_exhausted=bool(state.get("duration_probe_exhausted")),
        )
        if det is not None:
            stall_note = ""
            if stall_evidence:
                stall_note = (
                    f" stall_when={stall_evidence.get('stall_when')} "
                    f"call_limit_s={stall_evidence.get('call_limit_s')} "
                    f"stack_status={stall_evidence.get('stack_status')}"
                )
            parsed = TestDurationResult(
                finding_id=finding_id,
                classification=det,
                evidence_refs=StaticTriageEvidenceRef(),
                rationale=(
                    f"deterministic duration: {det} for {observation[:180]}.{stall_note}"
                ),
            )
            return {
                "verify_dispatch_path": "test",
                "test_duration_result": parsed.model_dump(mode="json"),
                **agent_session_patch(
                    state,
                    agent_id=None,
                    agent_id_key="test_duration_agent_id",
                ),
            }
        repo = str(state["repository_path"])
        package = build_test_triage_input_pack(
            repository_path=repo,
            item=finding_item,
            state=state,
        )
        package["duration_probe_exhausted"] = bool(state.get("duration_probe_exhausted"))
        prompt = get_prompt(input_pack_json=json.dumps(package, ensure_ascii=False, indent=2))
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("test_duration_stream_log") or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "test_duration"),
            ),
            prompt=prompt,
            agent_key="test_duration",
            tools=_READ_TOOLS,
            resume_agent_id=resume_when_same_finding(
                state,
                stored_agent_id=state.get("test_duration_agent_id"),
                bound_run_uuid=state.get("test_duration_session_run_uuid"),
                prior_result=state.get("test_duration_result"),
                finding_id=finding_id,
                unit_id=str(finding_item.get("unit_id") or finding_item.get("repair_unit_id") or ""),
            ),
            payload_model=TestDurationResult,
            format_instructions=schema_format_reminder(TestDurationResult),
            max_validation_attempts=self.validation_max_attempts,
        )
        parsed: TestDurationResult = outcome.parsed  # type: ignore[assignment]
        if state.get("duration_probe_exhausted") and parsed.classification == "PERSONAL_TIMEOUT":
            parsed = parsed.model_copy(update={"classification": "UNCLEAR"})
        parsed = parsed.model_copy(update={"finding_id": finding_id or parsed.finding_id})
        return {
            "verify_dispatch_path": "test",
            "test_duration_result": parsed.model_dump(mode="json"),
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="test_duration_agent_id",
            ),
        }

    async def triage_repair_unit(self, state: GraphState) -> dict[str, Any]:
        """Одна причинная группа, если весь repair unit — timeout/stall."""
        unit_item = state.get("dispatcher_current_item")
        if not isinstance(unit_item, dict):
            raise ValueError("dispatcher_current_item missing for duration repair unit")
        patch = await self.run(state)
        result = patch["test_duration_result"]
        classification = str(result.get("classification") or "UNCLEAR")
        group_class, repair_route = _group_route(classification)
        finding_ids = [str(x) for x in list(unit_item.get("finding_ids") or []) if str(x)]
        if not finding_ids:
            anchor = str(unit_item.get("anchor_unit_id") or result.get("finding_id") or "duration")
            finding_ids = [anchor]
        group = CausalRepairGroup(
            group_id="duration-1",
            finding_ids=finding_ids,
            classification=group_class,
            repair_route=repair_route,
            rationale=str(result.get("rationale") or classification),
        )
        triage = RepairUnitTriageResult(
            agent="test_triage",
            repair_unit_key=str(unit_item.get("repair_unit_key") or ""),
            anchor_finding_id=str(unit_item.get("anchor_unit_id") or finding_ids[0]),
            status="GROUPED",
            causal_groups=[group],
        )
        validate_repair_unit_triage_partition(
            triage,
            expected_finding_ids=finding_ids,
        )
        return {
            **patch,
            "dispatch_unit_triage_result": triage.model_dump(mode="json"),
            "dispatch_causal_groups": [group.model_dump(mode="json")],
            "dispatcher_unit_triage_complete": True,
            "dispatcher_group_index": 0,
            "dispatcher_group_phase": "repair",
            "verify_dispatch_path": "test",
        }


def _finding_item(item: dict[str, Any]) -> dict[str, Any]:
    findings = item.get("findings")
    if item.get("item_kind") == "repair_unit" and isinstance(findings, list) and findings:
        anchor = str(item.get("anchor_unit_id") or "")
        for row in findings:
            if isinstance(row, dict) and str(row.get("finding_id") or "") == anchor:
                return row
        first = findings[0]
        if isinstance(first, dict):
            return first
    return item


def _group_route(classification: str) -> tuple[str, str]:
    if classification == "HANG_PRODUCT":
        return "LOGIC", "static"
    if classification == "PERSONAL_TIMEOUT":
        return "TEST", "personal_timeout"
    if classification == "HANG_TEST":
        return "TEST", "test"
    return "UNCLEAR", "test"
