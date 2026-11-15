"""Validation hop retry для GroupErrorResult через ABCAdapter."""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import pytest

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    Finding,
    GroupedError,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.verify.group_error.normalize_triage import normalize_group_error_result


def _valid_triage_json() -> str:
    body = GroupErrorResult(
        analysis_identity=AnalysisIdentity(change_id="c1"),
        input_integrity=InputIntegrity(status="COMPLETE"),
        findings=[
            Finding(finding_id="F-0001", source_tool="pytest", observation="a"),
            Finding(finding_id="F-0002", source_tool="mypy", observation="b"),
        ],
        ungrouped_finding_ids=["F-0001", "F-0002"],
        triage_summary=TriageSummary(findings_total=2, ungrouped_total=2),
    )
    return body.model_dump_json()


def _duplicate_partition_json() -> str:
    dup = {
        "schema_version": "1.5",
        "agent": "group_error",
        "analysis_identity": {"identity_status": "PARTIAL", "change_id": "c1"},
        "input_integrity": {"status": "COMPLETE", "issues": []},
        "grouped_errors": [
            {
                "error_id": "E-1",
                "problem": "p1",
                "instruments": ["pytest"],
                "finding_ids": ["F-0001"],
            },
            {
                "error_id": "E-2",
                "problem": "p2",
                "instruments": ["pytest"],
                "finding_ids": ["F-0001"],
            },
        ],
        "finding_groups": [],
        "ungrouped_finding_ids": [],
        "additional_finding_links": [],
        "findings": [
            {
                "finding_id": "F-0001",
                "source_tool": "pytest",
                "observation": "a",
                "classification": "FACT",
            },
        ],
        "triage_summary": {
            "findings_total": 1,
            "finding_groups_total": 2,
            "ungrouped_total": 0,
        },
    }
    return json.dumps(dup)


class _DupThenOkAdapter(ABCAdapter):
    def __init__(self) -> None:
        self.calls = 0
        self.agent_id = "ge-hop-1"

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
        _ = state, tools, prompt, payload_model, resume_agent_id
        self.calls += 1
        text = _duplicate_partition_json() if self.calls == 1 else _valid_triage_json()
        return AgentResult(
            text=text,
            status="finished",
            agent_id=self.agent_id,
            run_id=f"run-{self.calls}",
        )


@pytest.mark.asyncio
async def test_group_error_adapter_retries_on_partition_validation_error() -> None:
    adapter = _DupThenOkAdapter()
    outcome = await adapter.run(
        {"workspace": "/tmp", "repository_path": "/tmp", "model": {"id": "x"}},
        prompt="initial",
        tools=(),
        payload_model=GroupErrorResult,
        max_validation_attempts=4,
    )
    assert adapter.calls == 2
    assert outcome.validation_attempts == 2
    parsed: GroupErrorResult = outcome.parsed  # type: ignore[assignment]
    normalized = normalize_group_error_result(parsed)
    assert len(normalized.grouped_errors) >= 1
