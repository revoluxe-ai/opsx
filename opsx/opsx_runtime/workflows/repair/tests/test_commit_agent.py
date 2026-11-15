"""Tests for the deterministic commit handoff agent."""

from __future__ import annotations

import asyncio

from agents.common.commit_agent import CommitAgent
from workflows.repair.schemas import FixEvidence, RepairState


def test_commit_agent_skips_when_workspace_is_not_git(tmp_path):
    state = RepairState(
        mission_id="mission-1",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        output_dir=str(tmp_path / "run"),
        fix=FixEvidence(
            status="FIXED",
            summary="fixed root cause",
            files_changed=["src/a.py"],
            notes="same change reason",
        ),
    )

    result = asyncio.run(CommitAgent().run(state))

    assert result["commit_status"] == "SKIPPED_NO_GIT"
    assert result["commit_files"] == ["src/a.py"]
    assert result["commit_sha"] is None
    # Receipt must be emitted even without a git repository.
    workers_dir = tmp_path / "run" / "workers"
    assert workers_dir.exists()
    assert any(workers_dir.glob("*.json"))


def test_get_prompt_includes_commit_message_task() -> None:
    from agents.common.commit_message.prompt.get_prompt import get_prompt

    text = get_prompt(
        input_pack_json="{}",
        commit_message_task="Коммит одной SUPPORTED-строки",
    )
    assert "Задача этого коммита" in text
    assert "Коммит одной SUPPORTED-строки" in text

