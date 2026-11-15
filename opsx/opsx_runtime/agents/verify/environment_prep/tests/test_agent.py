"""Тесты VerifyEnvironmentPrepAgent (mock adapter)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agents.state import GraphState
from agents.verify.environment_prep.agent import VerifyEnvironmentPrepAgent
from agents.verify.environment_prep.schemas import EnvironmentPrepResult
from agents.verify.group_error.gate_only_triage import build_gate_only_group_error_result


@dataclass
class _Outcome:
    parsed: EnvironmentPrepResult
    agent_id: str = "agent-1"
    run_id: str = "run-1"
    status: str = "finished"


class _StubAdapter:
    def __init__(self, result: EnvironmentPrepResult) -> None:
        self._result = result

    async def run(self, *args: Any, **kwargs: Any) -> _Outcome:
        _ = args, kwargs
        return _Outcome(parsed=self._result)


@pytest.mark.asyncio
async def test_environment_prep_no_action_without_cohorts(tmp_path):
    state: GraphState = {
        "repository_path": str(tmp_path),
        "change_id": "c1",
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
            "pyright": ["src/stroy/foo.py:1:1 - error: x"],
        },
    }
    analysis = build_gate_only_group_error_result(
        change_id="c1",
        execution_id=None,
        state=state,
    )
    state["analysis_result"] = analysis.model_dump(mode="json")
    agent = VerifyEnvironmentPrepAgent(_StubAdapter(EnvironmentPrepResult(status="FIXED", summary="x")))
    patch = await agent.run(state)
    assert patch["environment_prep_execution_status"] == "no_action"


@pytest.mark.asyncio
async def test_environment_prep_fixed_sets_dispatch_item_for_commit(monkeypatch):
    from pathlib import Path

    repo = Path(__file__).resolve().parents[6]
    state: GraphState = {
        "repository_path": str(repo),
        "change_id": "c1",
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
            "pytest": ["pytest: fixture 'repo_root' not found"],
        },
    }
    fixed = EnvironmentPrepResult(
        status="FIXED",
        summary="added repo_root fixture",
        files_changed=["tests/conftest.py"],
        gates_rerun_recommended=True,
        cohorts_addressed=["ENV-shared_fixtures"],
    )
    commit_calls: list[dict] = []

    async def _fake_commit(st, *, repair_adapter=None):
        _ = repair_adapter
        commit_calls.append(dict(st))
        return {"commit_status": "SKIPPED", "verify_finding_commit_outcome": {"status": "SKIPPED"}}

    monkeypatch.setattr(
        "workflows.verify.dispatch_cycle.commit_node.run_verify_finding_commit",
        _fake_commit,
    )
    agent = VerifyEnvironmentPrepAgent(_StubAdapter(fixed))
    patch = await agent.run(state)
    item = patch.get("dispatcher_current_item")
    assert isinstance(item, dict)
    assert item.get("finding_id") == "ENV-PREP"
    assert commit_calls
    assert isinstance(commit_calls[0].get("dispatcher_current_item"), dict)


@pytest.mark.asyncio
async def test_environment_prep_blocked_without_db_url(tmp_path):
    state: GraphState = {
        "repository_path": str(tmp_path),
        "change_id": "c1",
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
            "pytest": [
                "pytest: IngressStorageError: TEST_DATABASE_URL not set for integration",
            ],
        },
    }
    blocked = EnvironmentPrepResult(
        status="BLOCKED",
        summary="PG required; TEST_DATABASE_URL not set",
        environment_report="Set TEST_DATABASE_URL locally",
        gates_rerun_recommended=False,
    )
    agent = VerifyEnvironmentPrepAgent(_StubAdapter(blocked))
    patch = await agent.run(state)
    assert patch["environment_prep_result"]["status"] == "BLOCKED"
