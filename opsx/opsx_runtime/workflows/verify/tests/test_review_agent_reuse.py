"""One shared durable clean receipt for project-bug, bugbot and security."""
from __future__ import annotations

import subprocess
from importlib import import_module
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.adapters.abc_adapter import AgentResult
from agents.state import BugBotAnswer
from agents.verify.project_bug_hunt.schemas import ProjectBugHuntAnswer
from agents.verify.security_review.schemas import SecurityReviewAnswer

CASES = (
    ("project_bug_hunt", "ProjectBugHuntAgent", ProjectBugHuntAnswer, "project_bug", "HUNT_COMPLETE"),
    ("bugbot", "BugBotAgent", BugBotAnswer, "bugbot", "CLEAN"),
    ("security_review", "SecurityReviewAgent", SecurityReviewAnswer, "security", "SECURITY_COMPLETE"),
)


def _repo(root):
    change = root / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "design.md").write_text("behavior\n", encoding="utf-8")
    src = root / "src/stroy"
    src.mkdir(parents=True)
    (src / "work.py").write_text("x = 1\n", encoding="utf-8")
    for role in ("project_bug_hunt", "bugbot", "security_review"):
        policy = root / "opsx/opsx_runtime/agents/verify" / role / "agent.py"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("contract = 1\n", encoding="utf-8")
    for command in (
        ["git", "init"],
        ["git", "config", "user.name", "CI"],
        ["git", "config", "user.email", "ci@example.com"],
        ["git", "add", "."],
        ["git", "commit", "-m", "baseline"],
        ["git", "branch", "stage"],
    ):
        subprocess.run(command, cwd=root, capture_output=True, check=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("directory,klass,schema,role,success", CASES)
async def test_clean_review_reuses_without_new_sdk_call(
    tmp_path, monkeypatch, directory, klass, schema, role, success,
):
    _repo(tmp_path)
    module = import_module(f"agents.verify.{directory}.agent")
    monkeypatch.setattr(module, "get_prompt", lambda *args: "Stable reviewer prompt")
    answer = schema(status=success, findings=[])
    adapter = MagicMock()
    adapter.run = AsyncMock(return_value=AgentResult(
        text=answer.model_dump_json(), parsed=answer,
        run_id="run-a", agent_id="agent-a", status="finished",
    ))
    agent = getattr(module, klass)(adapter)
    state = {
        "repository_path": str(tmp_path), "change_id": "demo",
        "run_uuid": "exec-1", "model": {"id": "model-tier-1"},
        f"{role}_level": 1,
    }
    first = await agent.run(state)
    assert first["check_receipt"]["saved"] is True
    second = await agent.run({**state, "run_uuid": "exec-2"})
    assert second["check_receipt"]["source"] == "durable_pass"
    assert second["check_receipt"]["origin_execution_id"] == "exec-1"
    assert second["check_receipt"]["current_execution_id"] == "exec-2"
    assert second["execution_status"] == "REUSED_PASS"
    assert second["review"]["status"] == success
    assert second["run_id"] is None
    assert second["agent_id"] == ""
    assert adapter.run.await_count == 1

    # Same file tree with a different model tier is *not* the same reviewer.
    third = await agent.run({**state, "run_uuid": "exec-3", "model": {"id": "model-tier-2"}})
    assert third["check_receipt"]["source"] == "fresh_audit"
    assert adapter.run.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("directory,klass,schema,role,success", CASES)
async def test_findings_are_not_reused_as_clean(tmp_path, monkeypatch, directory, klass, schema, role, success):
    _repo(tmp_path)
    module = import_module(f"agents.verify.{directory}.agent")
    monkeypatch.setattr(module, "get_prompt", lambda *args: "Stable reviewer prompt")
    # A status with incomplete/blocked evidence must not be published as clean.
    if role == "bugbot":
        answer = schema(status="BLOCKED", findings=[], block_reason="cannot read diff")
    else:
        answer = schema(status="EVIDENCE_GAP", findings=[], block_reason="insufficient proof")
    adapter = MagicMock()
    adapter.run = AsyncMock(return_value=AgentResult(
        text=answer.model_dump_json(), parsed=answer,
        run_id="run-a", agent_id="agent-a", status="finished",
    ))
    agent = getattr(module, klass)(adapter)
    state = {
        "repository_path": str(tmp_path), "change_id": "demo",
        "run_uuid": "exec-1", "model": {"id": "model-tier-1"},
        f"{role}_level": 1,
    }
    first = await agent.run(state)
    second = await agent.run({**state, "run_uuid": "exec-2"})
    assert first["check_receipt"]["saved"] is False
    assert second["check_receipt"]["saved"] is False
    assert adapter.run.await_count == 2
