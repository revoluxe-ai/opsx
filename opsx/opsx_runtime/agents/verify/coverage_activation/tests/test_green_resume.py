"""Зелёный skip coverage_activation: apply commit vs no-op."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.verify.coverage_activation.agent import CoverageActivationAgent
from agents.verify.coverage_activation.green_resume import (
    coverage_activation_apply_invalidates_green,
    coverage_activation_complete_after_hop,
    coverage_activation_may_skip_green_resume,
    patch_coverage_activation_apply_code_changed,
)
from workflows.verify.apply_commit_node import run_verify_apply_commit
from workflows.verify.green_loop import commit_outcome_implies_code_changed


def test_may_skip_when_green_fingerprint_unchanged_and_apply_noop() -> None:
    state = {"coverage_activation_apply_code_changed": False}
    assert coverage_activation_may_skip_green_resume(
        state,
        prior_complete=True,
        prior_fingerprint="abc",
        fingerprint="abc",
    )


def test_may_not_skip_when_apply_commit_invalidates() -> None:
    state = {"coverage_activation_apply_code_changed": True}
    assert not coverage_activation_may_skip_green_resume(
        state,
        prior_complete=True,
        prior_fingerprint="abc",
        fingerprint="abc",
    )


def test_complete_preserved_on_skip() -> None:
    assert coverage_activation_complete_after_hop(
        verdict="COVERAGE_ACTIVATION_PASS",
        skipped=True,
        prior_complete=True,
    )


def test_complete_cleared_on_skip_without_prior_green() -> None:
    assert not coverage_activation_complete_after_hop(
        verdict="COVERAGE_ACTIVATION_PASS",
        skipped=True,
        prior_complete=False,
    )


def test_apply_invalidates_helper() -> None:
    assert coverage_activation_apply_invalidates_green(
        patch_coverage_activation_apply_code_changed(),
    )


def test_apply_commit_sets_coverage_activation_invalidate_flag() -> None:
    patch = {
        "commit_status": "COMMITTED",
        "commit_files": ["src/stroy/x.py"],
    }
    assert commit_outcome_implies_code_changed(patch)
    merged = {**patch, **patch_coverage_activation_apply_code_changed()}
    assert merged["coverage_activation_apply_code_changed"] is True


def test_apply_commit_no_files_does_not_set_invalidate_via_node() -> None:
    result = asyncio.run(run_verify_apply_commit({"verify_finding_fix": None}))
    assert result.get("coverage_activation_apply_code_changed") is not True


class _FailIfCalledAdapter(ABCAdapter):
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
        raise AssertionError("LLM adapter must not run on deterministic skip/full paths in this test")


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, capture_output=True)


def _commit_all(root: Path, message: str = "base") -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, capture_output=True)


def _write_strict_config(root: Path) -> None:
    body = {
        "include": ["src/stroy/base.py"],
        "exclude": ["**/vendor/**"],
        "ignore": [],
        "typeCheckingMode": "strict",
    }
    (root / "pyrightconfig_strict.json").write_text(json.dumps(body, indent=2), encoding="utf-8")


@pytest.fixture
def activation_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("BOUNDED_VERIFY_COVERAGE_BASE_REF", "HEAD")
    cap = root / "opsx/project"
    cap.mkdir(parents=True)
    (cap / "capabilities.yaml").write_text(
        "product:\n  source_roots:\n    - src/stroy\n",
        encoding="utf-8",
    )
    _git_init(root)
    (root / "src/stroy").mkdir(parents=True)
    (root / "src/stroy/base.py").write_text("def base() -> int:\n    return 1\n", encoding="utf-8")
    _write_strict_config(root)
    _commit_all(root, "base")
    (root / "src/stroy/new_module.py").write_text(
        "def new_fn() -> int:\n    return 2\n",
        encoding="utf-8",
    )
    return root


def test_agent_apply_noop_preserves_green_skip(activation_repo: Path) -> None:
    agent = CoverageActivationAgent(_FailIfCalledAdapter())
    base_state = {
        "change_id": "chg",
        "repository_path": str(activation_repo),
        "workspace": str(activation_repo),
    }
    first = asyncio.run(agent.run(base_state))
    assert first["coverage_activation_complete"] is True
    assert first.get("coverage_activation_skipped_resume") is not True

    second = asyncio.run(agent.run({**base_state, **first}))
    assert second.get("coverage_activation_skipped_resume") is True
    assert second["coverage_activation_complete"] is True


def test_agent_apply_code_change_forces_rerun(activation_repo: Path) -> None:
    agent = CoverageActivationAgent(_FailIfCalledAdapter())
    base_state = {
        "change_id": "chg",
        "repository_path": str(activation_repo),
        "workspace": str(activation_repo),
    }
    first = asyncio.run(agent.run(base_state))
    third = asyncio.run(
        agent.run(
            {
                **base_state,
                **first,
                **patch_coverage_activation_apply_code_changed(),
            },
        ),
    )
    assert third.get("coverage_activation_skipped_resume") is not True
    assert third["coverage_activation_complete"] is True
    assert third.get("coverage_activation_apply_code_changed") is False
