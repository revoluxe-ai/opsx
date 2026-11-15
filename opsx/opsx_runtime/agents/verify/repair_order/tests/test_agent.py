"""Tests for RepairOrderAgent (no live LLM)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.state import GraphState
from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    FindingGroup,
    GroupedError,
    InputIntegrity,
    GroupErrorResult,
    TriageSummary,
)
from agents.verify.repair_order.agent import RepairOrderAgent
from agents.verify.repair_order.prompt.get_prompt import get_prompt
from agents.verify.repair_order.schemas import RepairOrderAnswer


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


def _analysis_state_broken_coverage() -> dict[str, Any]:
    return GroupErrorResult(
        schema_version="1.5",
        analysis_identity=AnalysisIdentity(change_id="c1"),
        input_integrity=InputIntegrity(status="COMPLETE"),
        grouped_errors=[
            GroupedError(
                error_id="TG-001",
                problem="x",
                match_basis="y",
                instruments=["pytest", "mypy"],
                finding_ids=["F-0001", "F-0002"],
            ),
        ],
        findings=[
            _finding("F-0001", "pytest"),
            _finding("F-0002", "mypy"),
            _finding("F-0003", "pyright"),
        ],
        triage_summary=TriageSummary(findings_total=3, finding_groups_total=1, ungrouped_total=0),
    ).model_dump(mode="json")


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
        body = RepairOrderAnswer(
            ordered_unit_ids=["TG-001", "F-0003"],
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="rec-ro",
            run_id="run-rec-ro",
        )


@pytest.mark.asyncio
async def test_agent_baseline_on_skipped_integrity():
    adapter = _RecordingAdapter()
    agent = RepairOrderAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "skipped_integrity",
        "analysis_result": _analysis_state(),
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["repair_order_result"]["status"] == "SORTED"
    assert patch["repair_order_execution_status"] == "deterministic_baseline"


@pytest.mark.asyncio
async def test_agent_baseline_on_skipped_input_integrity():
    adapter = _RecordingAdapter()
    agent = RepairOrderAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "skipped_input_integrity",
        "analysis_result": _analysis_state(),
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["repair_order_result"]["status"] == "SORTED"


@pytest.mark.asyncio
async def test_agent_pause_on_partial_input_integrity():
    adapter = _RecordingAdapter()
    agent = RepairOrderAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "finished",
        "analysis_result": _analysis_state(
            input_integrity=InputIntegrity(status="PARTIAL", issues=["mypy incomplete"]),
        ),
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["repair_order_result"]["status"] == "PAUSE"
    assert "PARTIAL" in (patch["repair_order_result"]["pause_reason"] or "")


@pytest.mark.asyncio
async def test_agent_pause_on_invalid_input_integrity():
    adapter = _RecordingAdapter()
    agent = RepairOrderAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "finished",
        "analysis_result": _analysis_state(
            input_integrity=InputIntegrity(status="INVALID", issues=["counts mismatch"]),
        ),
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["repair_order_result"]["status"] == "PAUSE"
    assert "INVALID" in (patch["repair_order_result"]["pause_reason"] or "")


@pytest.mark.asyncio
async def test_agent_pause_on_broken_coverage():
    adapter = _RecordingAdapter()
    agent = RepairOrderAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "finished",
        "analysis_result": _analysis_state_broken_coverage(),
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["repair_order_result"]["status"] == "PAUSE"
    assert "coverage" in (patch["repair_order_result"]["pause_reason"] or "").lower()


@pytest.mark.asyncio
async def test_agent_baseline_by_default_without_llm_opt_in():
    adapter = _RecordingAdapter()
    agent = RepairOrderAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "finished",
        "analysis_result": _analysis_state(),
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["repair_order_execution_status"] == "deterministic_baseline"
    assert patch["repair_order_result"]["status"] == "SORTED"
    assert len(patch["repair_order_result"]["ordered_units"]) == 2


@pytest.mark.asyncio
async def test_agent_calls_llm_when_opt_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPSX_REPAIR_ORDER_LLM", "1")
    adapter = _RecordingAdapter()
    agent = RepairOrderAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "group_error_execution_status": "finished",
        "analysis_result": _analysis_state(),
    }
    patch = await agent.run(state)
    assert adapter.calls == 1
    assert patch["repair_order_result"]["status"] == "SORTED"
    assert patch["repair_order_result"]["source_identity"]["group_error_schema_version"] == "1.5"
    assert len(patch["repair_order_result"]["ordered_units"]) == 2
    units = patch["repair_order_result"]["ordered_units"]
    assert units[0]["unit_id"] == "TG-001"
    assert units[1]["unit_id"] == "F-0003"
    assert units[0]["finding_ids"] == ["F-0001", "F-0002"]


def test_prompt_requires_id_only_order_payload():
    prompt = get_prompt(repair_order_package_json="{}")
    for needle in (
        "Repair Order Agent",
        "grouped_errors",
        "error_id",
        "EXPLICIT_DEPENDENCY",
        "FINDING_DEFINITENESS",
        "POTENTIAL_EFFECT",
        "LIMITED_SCOPE",
        "STABLE_TIE_BREAK",
        "лексикографическ",
        "ordered_unit_ids",
        "Regroup",
        "static_repair_queue",
    ):
        assert needle.lower() in prompt.lower() or needle in prompt
    assert "ordered_units[]" not in prompt or "запрещено" in prompt.lower()
