"""execute_project_bug_hunt / orch must heartbeat during long SDK review hops."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest
from temporalio.testing import ActivityEnvironment

from activities import (
    ActivityConfig,
    configure_activities,
    execute_project_bug_hunt,
    execute_project_bug_hunt_orch,
)
from models import RepairRunResult, ReviewRunResult


@pytest.mark.asyncio
async def test_project_bug_hunt_review_uses_heartbeat_wrapper_not_bare_to_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            lane="project-bug-hunt",
            change="invest-yalta-max-funnel",
        )
    )
    to_thread_calls: list = []

    async def _track_to_thread(fn):
        to_thread_calls.append(fn)
        return fn()

    monkeypatch.setattr(asyncio, "to_thread", _track_to_thread)
    hb_calls: list = []

    async def _fake_hb(fn, **kwargs):
        hb_calls.append(kwargs.get("details"))
        return fn()

    monkeypatch.setattr("activities._run_blocking_with_heartbeat", _fake_hb)

    class _Adapter:
        def session_id(self):
            return "agent-mock"

        timeout_seconds = None

    monkeypatch.setattr(
        "activities._get_or_restore_adapter",
        lambda _cfg: _Adapter(),
    )

    review = ReviewRunResult(
        completed=True,
        clean=False,
        findings=[],
        actionable_findings=[],
        repairable_findings=[],
        excluded_findings=[],
        fingerprint="fp",
        raw_output="",
    )
    monkeypatch.setattr(
        "activities.ReviewRepairLoop.run_single_review",
        lambda self: review,
    )

    env = ActivityEnvironment()
    out = await env.run(execute_project_bug_hunt, {"review_number": 1, "change": "x"})
    assert out["fingerprint"] == "fp"
    assert to_thread_calls == []
    assert hb_calls
    assert hb_calls[0]["step"] == "execute_project_bug_hunt"


@pytest.mark.asyncio
async def test_project_bug_hunt_review_emits_multiple_heartbeats(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            lane="project-bug-hunt",
            change="invest-yalta-max-funnel",
        )
    )

    class _Adapter:
        def session_id(self):
            return "agent-slow"

        timeout_seconds = None

    monkeypatch.setattr("activities._get_or_restore_adapter", lambda _cfg: _Adapter())

    def _slow_review(self):
        time.sleep(0.12)
        return ReviewRunResult(
            completed=True,
            clean=True,
            findings=[],
            actionable_findings=[],
            repairable_findings=[],
            excluded_findings=[],
            fingerprint="ok",
            raw_output="",
        )

    monkeypatch.setattr("activities.ReviewRepairLoop.run_single_review", _slow_review)
    monkeypatch.setattr("activities.PROJECT_BUG_HUNT_HEARTBEAT_INTERVAL_SECONDS", 0.04)

    heartbeats: list = []
    env = ActivityEnvironment()
    with patch("activities.activity.heartbeat", side_effect=lambda d: heartbeats.append(d)):
        await env.run(execute_project_bug_hunt, {"review_number": 1, "change": "x"})

    assert len(heartbeats) >= 2
    assert heartbeats[0]["phase"] == "project_bug_hunt"


@pytest.mark.asyncio
async def test_project_bug_hunt_orch_uses_heartbeat_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            lane="project-bug-hunt",
            change="invest-yalta-max-funnel",
        )
    )
    hb_steps: list = []

    async def _fake_hb(fn, **kwargs):
        details = kwargs.get("details") or {}
        hb_steps.append(details.get("step"))
        return fn()

    monkeypatch.setattr("activities._run_blocking_with_heartbeat", _fake_hb)

    class _Adapter:
        def session_id(self):
            return "agent-mock"

        timeout_seconds = None

    monkeypatch.setattr("activities._get_or_restore_adapter", lambda _cfg: _Adapter())
    monkeypatch.setattr(
        "activities.ReviewRepairLoop.run_single_repair",
        lambda self: RepairRunResult(
            completed=True,
            bug_batch_clear=True,
            uncertain=False,
            unresolved=[],
            running_worker=None,
            repaired_this_batch=0,
            raw_output="",
        ),
    )

    env = ActivityEnvironment()
    await env.run(execute_project_bug_hunt_orch, {"repair_number": 1, "change": "x"})
    assert hb_steps == ["execute_project_bug_hunt_orch"]
