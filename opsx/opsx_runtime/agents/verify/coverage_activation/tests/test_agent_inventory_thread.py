"""CoverageActivationAgent: тяжёлый git-inventory не блокирует event loop."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.verify.coverage_activation.agent import CoverageActivationAgent
from agents.verify.coverage_activation.git_inventory import ChangeInventory


def _setup_change_root(root) -> None:
    change = root / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "design.md").write_text("contract\n", encoding="utf-8")
    policy = root / "opsx/opsx_runtime/agents/verify/coverage_activation/agent.py"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("contract = 1\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_collect_change_inventory_runs_in_to_thread(tmp_path, monkeypatch) -> None:
    """``collect_change_inventory`` вызывается через ``asyncio.to_thread``."""
    inv = ChangeInventory(
        base_sha="abc",
        audit_target="WORKTREE",
        current_changed=(),
        historical_touched=(),
        deleted_paths=(),
        status="NOT_APPLICABLE",
        block_reason="",
    )
    def fake_collect_change_inventory(*_a, **_k):
        return inv

    monkeypatch.setattr(
        "agents.verify.coverage_activation.agent.collect_change_inventory",
        fake_collect_change_inventory,
    )
    seen: list[str] = []

    async def tracking_to_thread(fn, /, *args, **kwargs):
        seen.append(getattr(fn, "__name__", str(fn)))
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", tracking_to_thread)
    _setup_change_root(tmp_path)

    adapter = MagicMock()
    adapter.run = AsyncMock()
    state = {
        "repository_path": str(tmp_path),
        "change_id": "demo",
        "run_uuid": "exec-1",
        "model": {"id": "composer-2.5"},
    }
    patch = await CoverageActivationAgent(adapter).run(state)
    assert len(seen) == 1
    assert "collect_change_inventory" in seen[0]
    assert patch["coverage_activation_verdict"] == "COVERAGE_ACTIVATION_PASS"
    adapter.run.assert_not_awaited()
