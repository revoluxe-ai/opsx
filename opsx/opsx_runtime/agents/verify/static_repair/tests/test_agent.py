"""Tests for ``StaticRepairAgent``."""

from __future__ import annotations

import pytest

from agents.verify.static_repair.agent import StaticRepairAgent


@pytest.mark.asyncio
async def test_static_repair_skips_on_pass():
    agent = StaticRepairAgent()
    patch = await agent.run(
        {
            "change_id": "chg",
            "quality_gate_verdict": "PASS",
            "quality_gate_diagnostics": {"pytest": [], "pyright": [], "mypy": [], "total_count": 0},
        },
    )
    assert patch["static_repair_status"] == "SKIPPED_PASS"
    assert patch["static_repair_issue_count"] == 0


@pytest.mark.asyncio
async def test_static_repair_queues_diagnostics():
    agent = StaticRepairAgent()
    patch = await agent.run(
        {
            "change_id": "chg",
            "quality_gate_verdict": "FAIL",
            "quality_gate_diagnostics": {
                "pytest": [],
                "pyright": ["a.py:1: err"],
                "mypy": ["b.py:2: err [code]"],
                "total_count": 2,
            },
        },
    )
    assert patch["static_repair_status"] == "REPAIR_QUEUE"
    assert patch["static_repair_issue_count"] == 2
    assert len(patch["static_repair_queue"]) == 2
