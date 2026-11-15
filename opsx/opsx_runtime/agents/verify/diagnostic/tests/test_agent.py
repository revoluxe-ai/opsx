"""Tests for DiagnosticAgent (no live LLM)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.state import GraphState
from agents.verify.diagnostic.agent import DiagnosticAgent
from agents.verify.diagnostic.prompt.get_prompt import get_prompt
from agents.verify.diagnostic.schemas import (
    DiagnosticAnswer,
    RepairUnitAnswer,
)
from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    FindingGroup,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.verify.repair_order.schemas import OrderedUnit, RepairOrderResult, SourceIdentity


def _finding(finding_id: str, source_tool: str) -> Finding:
    return Finding(
        finding_id=finding_id,
        source_tool=source_tool,  # type: ignore[arg-type]
        repair_workflow="WF_UNKNOWN_GATE",
    )


def _analysis_state(**updates: object) -> dict[str, Any]:
    body = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1"),
        input_integrity=InputIntegrity(status="COMPLETE"),
        finding_groups=[
            FindingGroup(
                group_id="TG-001",
                finding_ids=["F-0001", "F-0002"],
                matched_problem="x",
                match_basis="y",
                diagnostic_sources=["pytest", "mypy"],
            ),
        ],
        ungrouped_finding_ids=["F-0003"],
        findings=[
            _finding("F-0001", "pytest"),
            _finding("F-0002", "mypy"),
            _finding("F-0003", "pyright"),
        ],
        triage_summary=TriageSummary(
            findings_total=3,
            finding_groups_total=1,
            ungrouped_total=1,
        ),
    )
    if updates:
        body = body.model_copy(update=updates)
    return normalize_group_error_result(body).model_dump(mode="json")


def _repair_order_state(**updates: object) -> dict[str, Any]:
    body = RepairOrderResult(
        status="SORTED",
        source_identity=SourceIdentity(change_id="c1"),
        ordered_units=[
            OrderedUnit(
                position=1,
                unit_type="GROUP",
                unit_id="TG-001",
                finding_ids=["F-0001", "F-0002"],
                placement_rationale="stub",
            ),
            OrderedUnit(
                position=2,
                unit_type="GROUP",
                unit_id="F-0003",
                finding_ids=["F-0003"],
                placement_rationale="stub",
            ),
        ],
    )
    if updates:
        body = body.model_copy(update=updates)
    return body.model_dump(mode="json")


class _RecordingAdapter(ABCAdapter):
    def __init__(self) -> None:
        self.calls = 0

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
        read_scope: Any | None = None,
    ) -> AgentResult:
        self.calls += 1
        body = DiagnosticAnswer(
            status="DIAGNOSED",
            anchor_unit_id="TG-001",
            repair_unit=RepairUnitAnswer(
                ordered_positions=[1],
                affected_areas=["application_code"],
                root_cause_summary="stub root cause",
                change_boundaries="stub boundaries",
            ),
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="rec-diag",
            run_id="run-rec-diag",
        )


@pytest.mark.asyncio
async def test_agent_pause_missing_repair_order():
    adapter = _RecordingAdapter()
    agent = DiagnosticAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "finished",
        "repair_order_execution_status": "finished",
        "analysis_result": _analysis_state(),
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["diagnostic_result"]["status"] == "PAUSE"
    assert "missing" in (patch["diagnostic_result"]["pause_reason"] or "").lower()


@pytest.mark.asyncio
async def test_agent_pause_on_repair_order_pause():
    adapter = _RecordingAdapter()
    agent = DiagnosticAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "finished",
        "analysis_result": _analysis_state(),
        "repair_order_result": _repair_order_state(
            status="PAUSE",
            pause_reason="upstream",
            ordered_units=[],
        ),
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["diagnostic_result"]["status"] == "PAUSE"
    assert "repair_order paused" in (patch["diagnostic_result"]["pause_reason"] or "")


@pytest.mark.asyncio
async def test_agent_pause_on_empty_queue():
    adapter = _RecordingAdapter()
    agent = DiagnosticAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "finished",
        "repair_order_execution_status": "finished",
        "analysis_result": _analysis_state(),
        "repair_order_result": _repair_order_state(ordered_units=[]),
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["diagnostic_result"]["status"] == "PAUSE"
    assert "empty" in (patch["diagnostic_result"]["pause_reason"] or "").lower()


@pytest.mark.asyncio
async def test_agent_pause_on_skipped_integrity():
    adapter = _RecordingAdapter()
    agent = DiagnosticAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "skipped_integrity",
        "analysis_result": _analysis_state(),
        "repair_order_result": _repair_order_state(),
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["diagnostic_result"]["status"] == "PAUSE"
    assert "skipped" in (patch["diagnostic_result"]["pause_reason"] or "").lower()


@pytest.mark.asyncio
async def test_agent_per_finding_when_stale_skip_but_handoff_complete():
    """Устаревший skipped_input_integrity не блокирует, если handoff уже COMPLETE."""
    adapter = _RecordingAdapter()
    agent = DiagnosticAgent(adapter)
    node = "tests/unit/foo.py::test_bar"
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "verify_dispatch_mode": "per_finding",
        "group_error_execution_status": "skipped_input_integrity",
        "quality_gate_diagnostics": {
            "pytest": [f"pytest: {node} - AssertionError: boom"],
            "input_integrity_status": "PARTIAL",
            "input_integrity_issues": ["legacy partial"],
            "quality_gate_raw": {"pytest": {"exit_code": 1}},
        },
        "analysis_result": _analysis_state(),
        "repair_order_result": _repair_order_state(),
        "dispatcher_resolved_finding_ids": [],
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["diagnostic_result"]["status"] == "DIAGNOSED"
    assert patch["diagnostic_result"]["repair_unit"] is None


@pytest.mark.asyncio
async def test_agent_per_finding_clears_stale_resolved_after_failed_batch_recheck():
    """FULL_SUITE после batch recheck FAIL: сброс resolved, повтор dispatch с головы очереди."""
    adapter = _RecordingAdapter()
    agent = DiagnosticAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "verify_dispatch_mode": "per_finding",
        "batch_recheck_count": 1,
        "quality_gate_verdict": "FAIL",
        "group_error_execution_status": "finished",
        "repair_order_execution_status": "finished",
        "analysis_result": _analysis_state(),
        "repair_order_result": _repair_order_state(),
        "dispatcher_resolved_finding_ids": ["F-0001", "F-0002", "F-0003"],
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch.get("dispatcher_resolved_finding_ids") == []
    assert patch["diagnostic_result"]["status"] == "DIAGNOSED"
    assert patch["diagnostic_result"]["repair_unit"] is None
    assert patch["diagnostic_result"]["anchor_unit_id"] == "TG-001"


@pytest.mark.asyncio
async def test_agent_calls_llm_and_validates():
    adapter = _RecordingAdapter()
    agent = DiagnosticAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "verify_dispatch_mode": "repair_unit",
        "group_error_execution_status": "finished",
        "repair_order_execution_status": "finished",
        "analysis_result": _analysis_state(),
        "repair_order_result": _repair_order_state(),
    }
    patch = await agent.run(state)
    assert adapter.calls == 1
    assert patch["diagnostic_result"]["status"] == "DIAGNOSED"
    assert patch["diagnostic_result"]["anchor_unit_id"] == "TG-001"
    assert patch["diagnostic_result"]["repair_unit"]["unit_ids"] == ["TG-001"]
    assert patch["diagnostic_result"]["remaining_queue"][0]["unit_id"] == "F-0003"


def test_prompt_mentions_boundaries_and_group_error():
    prompt = get_prompt(diagnostic_package_json="{}")
    for needle in (
        "Diagnostic Agent",
        "Group Error Agent",
        "Repair Order Agent",
        "Repair Unit",
        "merged_unit_linkages",
        "ordered_positions",
        "findings_embed",
        "Dispatcher",
        "тот же файл",
        "root_cause_summary",
        "affected_areas",
    ):
        assert needle.lower() in prompt.lower() or needle in prompt
