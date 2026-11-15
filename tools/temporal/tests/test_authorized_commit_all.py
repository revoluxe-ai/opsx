"""Authorized commit-all adapter routing (not SDK READ_ONLY session)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from authorized_commit_all import (
    configure_commit_all_adapter_factory,
    make_authorized_commit_all_adapter,
    reset_commit_all_adapter_factory,
)
from commit_materialization import current_head, materialize_if_needed
from config import COMMIT_ALL_PROMPT
from cursor_adapter import PromptInvocation
from models import RepairRunResult


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "app.js").write_text("BUG\n", encoding="utf-8")
    _git(repo, "add", "app.js")
    _git(repo, "commit", "-m", "seed")
    return repo


class _GitCommitAllAdapter:
    """Fixture adapter: real git commit on COMMIT_ALL_PROMPT."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.invoked = False

    def session_id(self) -> str:
        return "fixture-commit-all"

    def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
        assert prompt == COMMIT_ALL_PROMPT
        assert step == "commit-all"
        self.invoked = True
        _git(self.repo, "add", "app.js")
        _git(self.repo, "commit", "-m", "fixture materialize")
        return PromptInvocation(
            prompt=prompt,
            raw_output="STATUS: COMPLETE\n",
            exit_code=0,
            session_id=self.session_id(),
        )


def test_make_authorized_commit_all_uses_injected_factory(tmp_path: Path):
    repo = _init_repo(tmp_path)
    head_before = current_head(repo)
    (repo / "app.js").write_text("FIXED\n", encoding="utf-8")
    adapter = _GitCommitAllAdapter(repo)

    def factory(repo_path, *, workflow_key, model_config=None):
        assert Path(repo_path) == repo
        assert "commit-all" in workflow_key
        return adapter

    configure_commit_all_adapter_factory(factory)
    try:
        authorized = make_authorized_commit_all_adapter(
            repo,
            workflow_key="wf:test:commit-all",
            model_config=None,
        )
        result = materialize_if_needed(
            repo_path=repo,
            adapter=authorized,
            repair=RepairRunResult(
                completed=True,
                bug_batch_clear=True,
                unresolved=[],
                running_worker=None,
                raw_output="",
                repaired_this_batch=True,
            ),
        )
    finally:
        reset_commit_all_adapter_factory()

    assert adapter.invoked
    assert result.ok is True
    assert result.status == "COMPLETE"
    assert result.head_before == head_before
    assert result.head_after != head_before
    assert result.candidate_transitioned is True


@pytest.mark.asyncio
async def test_execute_commit_materialize_activity_real_git(tmp_path: Path):
    from temporalio.testing import ActivityEnvironment

    from activities import (
        ActivityConfig,
        configure_activities,
        execute_commit_materialize,
    )

    repo = _init_repo(tmp_path)
    head_before = current_head(repo)
    (repo / "app.js").write_text("FIXED\n", encoding="utf-8")
    adapter_holder: dict[str, _GitCommitAllAdapter] = {}

    def factory(repo_path, *, workflow_key, model_config=None):
        holder = _GitCommitAllAdapter(Path(repo_path))
        adapter_holder["adapter"] = holder
        return holder

    configure_commit_all_adapter_factory(factory)
    configure_activities(ActivityConfig(mode="real", repo_path=str(repo), lane="bugbot"))
    try:
        env = ActivityEnvironment()
        payload = await env.run(
            execute_commit_materialize,
            {
                "repair": {
                    "completed": True,
                    "bug_batch_clear": True,
                    "unresolved": [],
                    "running_worker": None,
                    "raw_output": "",
                    "repaired_this_batch": True,
                },
            },
        )
    finally:
        reset_commit_all_adapter_factory()

    assert payload["ok"] is True
    assert payload["status"] == "COMPLETE"
    assert payload["head_before"] == head_before
    assert payload["head_after"] != head_before
    assert adapter_holder["adapter"].invoked
