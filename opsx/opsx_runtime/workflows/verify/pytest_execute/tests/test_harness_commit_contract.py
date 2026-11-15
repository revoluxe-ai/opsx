"""Контракт commit после harness: ``dispatcher_current_item`` обязателен."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.verify.pytest_run_health import CollectHealthResult
from workflows.verify.pytest_execute.invoke import harness_repair_and_commit


@pytest.mark.asyncio
async def test_run_verify_finding_commit_called_with_item() -> None:
    harness = MagicMock()
    harness.run = AsyncMock(
        return_value={
            "test_harness_repair_result": {"status": "FIXED"},
            "verify_finding_fix": {"files_changed": ["tests/a.py"]},
        },
    )
    collect = CollectHealthResult(
        ok=False, exit_code=2, command=[], log_tail="SyntaxError", duration_ms=1,
    )
    commit = AsyncMock(return_value={"commit_status": "COMMITTED"})
    state = {"repository_path": "/repo", "change_id": "x"}
    with patch(
        "workflows.verify.dispatch_cycle.commit_node.run_verify_finding_commit",
        commit,
    ):
        await harness_repair_and_commit(
            state=state,
            collect=collect,
            modules=["tests"],
            harness_agent=harness,
            repair_adapter=object(),
        )
    commit.assert_awaited_once()
    call_state = commit.await_args.args[0]
    item = call_state.get("dispatcher_current_item")
    assert isinstance(item, dict)
    assert item.get("finding_id") == "HARNESS-EXEC"
    assert item.get("path")
