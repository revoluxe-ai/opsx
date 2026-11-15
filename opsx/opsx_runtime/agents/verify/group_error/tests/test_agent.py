"""Tests for GroupErrorAgent (no live LLM)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from unittest.mock import AsyncMock, patch

import pytest

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.state import GraphState
from agents.verify.group_error.agent import (
    GROUP_ERROR_PARTITION_MISMATCH_AFTER_GATES,
    GroupErrorAgent,
)
from agents.verify.group_error.tests.test_validation_hop import (
    _duplicate_partition_json,
    _valid_triage_json,
)
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    InputIntegrity,
    GroupErrorResult,
    TriageSummary,
)
from agents.verify.group_error.prompt.get_prompt import get_prompt


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
        body = GroupErrorResult(
            analysis_identity=AnalysisIdentity(change_id="x"),
            input_integrity=InputIntegrity(status="COMPLETE"),
            ungrouped_finding_ids=[],
            triage_summary=TriageSummary(),
        )
        return AgentResult(
            text=body.model_dump_json(),
            status="finished",
            agent_id="rec",
            run_id="run-rec",
        )


@pytest.mark.asyncio
async def test_agent_skips_llm_on_integrity_block():
    adapter = _RecordingAdapter()
    agent = GroupErrorAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "execution_status": "INVALID",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {
            "pytest": ["fail"],
            "pyright": [],
            "mypy": [],
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
        },
        "quality_gate_raw": {
            "pytest": {"command": [], "exit_code": 1, "duration_ms": 1},
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 0, "duration_ms": 1},
        },
    }
    patch_result = await agent.run(state)
    assert adapter.calls == 0
    assert patch_result["group_error_execution_status"] == "skipped_integrity"
    assert patch_result["analysis_result"]["input_integrity"]["status"] == "INVALID"
    assert patch_result["analysis_result"]["schema_version"] == "1.5"
    assert len(patch_result["analysis_result"]["grouped_errors"]) == 1
    assert patch_result["analysis_result"]["finding_groups"] == []


@pytest.mark.asyncio
async def test_agent_uses_gate_only_by_default_when_integrity_ok():
    adapter = _RecordingAdapter()
    agent = GroupErrorAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
        },
        "quality_gate_raw": {
            "pytest": {"command": [], "exit_code": 0, "duration_ms": 1},
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 0, "duration_ms": 1},
        },
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["group_error_execution_status"] == "deterministic_gate_only"
    assert "analysis_result" in patch


@pytest.mark.asyncio
async def test_agent_calls_llm_when_opt_in(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPSX_GROUP_ERROR_LLM", "1")
    adapter = _RecordingAdapter()
    agent = GroupErrorAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
        },
        "quality_gate_raw": {
            "pytest": {"command": [], "exit_code": 0, "duration_ms": 1},
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 0, "duration_ms": 1},
        },
    }
    await agent.run(state)
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_triage_does_not_run_subprocess_gates():
    """GroupErrorAgent не запускает pytest/pyright/mypy — только читает diagnostics из state."""
    adapter = _RecordingAdapter()
    agent = GroupErrorAgent(adapter)
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
        },
        "quality_gate_raw": {
            "pytest": {"command": [], "exit_code": 0, "duration_ms": 1},
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 0, "duration_ms": 1},
        },
    }
    patch = await agent.run(state)
    assert adapter.calls == 0
    assert patch["group_error_execution_status"] == "deterministic_gate_only"


class _DupThenOkAdapter(_RecordingAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.agent_id = "ge-agent-retry"

    async def _run_turn(
        self,
        state,
        *,
        prompt: str,
        tools,
        payload_model=None,
        resume_agent_id: str | None = None,
        read_scope: Any | None = None,
    ) -> AgentResult:
        self.calls += 1
        text = _duplicate_partition_json() if self.calls == 1 else _valid_triage_json()
        return AgentResult(
            text=text,
            status="finished",
            agent_id=self.agent_id,
            run_id=f"run-{self.calls}",
        )


@pytest.mark.asyncio
async def test_agent_llm_partition_fallback_returns_gate_only(
    monkeypatch: pytest.MonkeyPatch,
):
    """При invalid partition — gate-only ``analysis_result``, не PAUSE."""
    monkeypatch.setenv("OPSX_GROUP_ERROR_LLM", "1")

    adapter = _RecordingAdapter()
    agent = GroupErrorAgent(adapter)
    gate_findings = [
        Finding(finding_id="F-0001", source_tool="pytest", observation="a"),
        Finding(finding_id="F-0002", source_tool="mypy", observation="b"),
    ]
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
            "pytest": ["fail: a"],
        },
        "quality_gate_raw": {
            "pytest": {"command": [], "exit_code": 1, "duration_ms": 1},
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 0, "duration_ms": 1},
        },
    }
    with (
        patch(
            "agents.verify.group_error.gate_only_triage.findings_from_gate_state",
            return_value=gate_findings,
        ),
        patch(
            "agents.verify.group_error.agent.findings_from_gate_state",
            return_value=gate_findings,
        ),
        patch(
            "agents.verify.group_error.agent.validate_group_error_triage_invariants",
            side_effect=ValueError("partition mismatch"),
        ),
    ):
        agent_patch = await agent.run(state)
    assert agent_patch["group_error_execution_status"] == "fallback_partition"
    assert "analysis_result" in agent_patch
    assert len(agent_patch["analysis_result"]["grouped_errors"]) == 2
    assert GROUP_ERROR_PARTITION_MISMATCH_AFTER_GATES in str(
        agent_patch.get("owner_attention") or "",
    )


@pytest.mark.asyncio
async def test_agent_retries_llm_on_duplicate_partition_triage(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPSX_GROUP_ERROR_LLM", "1")
    adapter = _DupThenOkAdapter()
    agent = GroupErrorAgent(adapter)
    gate_findings = [
        Finding(finding_id="F-0001", source_tool="pytest", observation="a"),
        Finding(finding_id="F-0002", source_tool="mypy", observation="b"),
    ]
    state: GraphState = {
        "change_id": "c1",
        "repository_path": ".",
        "quality_gates_execution_status": "DETERMINISTIC",
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
            "pytest": ["fail: a"],
        },
        "quality_gate_raw": {
            "pytest": {"command": [], "exit_code": 1, "duration_ms": 1},
            "pyright": {"command": [], "exit_code": 0, "duration_ms": 1},
            "mypy": {"command": [], "exit_code": 0, "duration_ms": 1},
        },
    }
    with patch(
        "agents.verify.group_error.agent.findings_from_gate_state",
        return_value=gate_findings,
    ):
        agent_patch = await agent.run(state)
    assert adapter.calls == 2
    assert agent_patch["group_error_validation_attempts"] == 2
    assert "analysis_result" in agent_patch


def test_prompt_contains_grouped_errors_semantics():
    prompt = get_prompt(verification_package_json="{}")
    for needle in (
        "grouped_errors",
        "instruments",
        "execution-integrity",
        "static_repair_queue",
        "match_basis",
    ):
        assert needle.lower() in prompt.lower() or needle in prompt
    assert "root cause" in prompt.lower()
