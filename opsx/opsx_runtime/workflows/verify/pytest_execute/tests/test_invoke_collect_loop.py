"""Collect-loop и cap exhausted для ``run_pytest_execute_sync``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.verify.pytest_run_health import CollectHealthResult
from workflows.verify.pytest_execute.invoke import harness_repair_and_commit, run_pytest_execute_sync
from workflows.verify.pytest_execute.schemas import PytestExecuteRequest


def test_harness_fixed_second_collect_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_collect(repo_root, modules, *, timeout_seconds=600):
        calls["n"] += 1
        return CollectHealthResult(
            ok=calls["n"] > 1,
            exit_code=0 if calls["n"] > 1 else 2,
            command=["pytest", "--collect-only"],
            log_tail="SyntaxError" if calls["n"] == 1 else "",
            duration_ms=1,
        )

    harness = MagicMock()
    harness.run = AsyncMock(
        return_value={
            "test_harness_repair_result": {"status": "FIXED"},
            "verify_finding_fix": {"files_changed": ["tests/a.py"]},
        },
    )

    monkeypatch.setattr(
        "workflows.verify.pytest_execute.invoke.run_collect_only",
        fake_collect,
    )
    monkeypatch.setattr(
        "workflows.verify.pytest_execute.invoke._run_pytest_after_collect",
        lambda *a, **k: (
            __import__(
                "workflows.verify.gate_repair.schemas",
                fromlist=["GateCheckResult"],
            ).GateCheckResult(
                check_id="pytest",
                command=["pytest"],
                exit_code=0,
                duration_ms=1,
            ),
            "ok",
        ),
    )
    monkeypatch.setattr(
        "workflows.verify.pytest_execute.invoke._harness_commit",
        AsyncMock(return_value={}),
    )

    result = run_pytest_execute_sync(
        tmp_path,
        PytestExecuteRequest(scope_kind="FULL_SUITE", max_harness_attempts=3),
        state={"repository_path": str(tmp_path), "change_id": "x"},
        harness_repair_agent=harness,
    )
    assert result.collect_proof.collect_ok
    assert result.harness_attempts == 1
    harness.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_harness_commit_receives_dispatcher_item() -> None:
    seen: dict[str, object] = {}

    harness = MagicMock()
    harness.run = AsyncMock(
        return_value={
            "test_harness_repair_result": {"status": "FIXED"},
            "verify_finding_fix": {"files_changed": ["tests/x.py"]},
        },
    )
    collect = CollectHealthResult(
        ok=False, exit_code=2, command=[], log_tail="err", duration_ms=1,
    )
    import workflows.verify.pytest_execute.invoke as invoke_mod

    async def fake_commit(state, *, repair_adapter):
        seen["item"] = state.get("dispatcher_current_item")
        seen["has_fix"] = bool(state.get("verify_finding_fix"))
        return {"commit_status": "SKIPPED"}

    invoke_mod._harness_commit = fake_commit  # type: ignore[assignment]
    state = {"repository_path": "/repo", "change_id": "c"}
    await harness_repair_and_commit(
        state=state,
        collect=collect,
        modules=["tests"],
        harness_agent=harness,
        repair_adapter=object(),
    )
    assert isinstance(seen.get("item"), dict)
    assert seen["item"].get("finding_id") == "HARNESS-EXEC"
    assert seen["item"].get("source_tool") == "pytest"
    assert seen.get("has_fix") is True


@pytest.mark.asyncio
async def test_sync_raises_inside_running_loop() -> None:
    async def _inner() -> None:
        with pytest.raises(RuntimeError, match="active event loop"):
            run_pytest_execute_sync(
                Path("/tmp"),
                PytestExecuteRequest(scope_kind="FULL_SUITE"),
            )

    await _inner()


def test_cap_exhausted_pause_without_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = MagicMock()
    harness.run = AsyncMock(
        return_value={"test_harness_repair_result": {"status": "FAILED"}},
    )

    monkeypatch.setattr(
        "workflows.verify.pytest_execute.invoke.run_collect_only",
        lambda *a, **k: CollectHealthResult(
            ok=False,
            exit_code=2,
            command=[],
            log_tail="ERROR",
            duration_ms=1,
        ),
    )
    run_called = {"v": False}

    def fail_if_run(*a, **k):
        run_called["v"] = True
        raise AssertionError("pytest run should not execute")

    monkeypatch.setattr(
        "workflows.verify.pytest_execute.invoke._run_pytest_after_collect",
        fail_if_run,
    )

    result = run_pytest_execute_sync(
        tmp_path,
        PytestExecuteRequest(scope_kind="FULL_SUITE", max_harness_attempts=2),
        harness_repair_agent=harness,
    )
    assert result.status == "PAUSE"
    assert result.owner_attention and "HARNESS_COLLECTION_BLOCKER" in result.owner_attention
    assert not run_called["v"]
    assert harness.run.await_count == 2
