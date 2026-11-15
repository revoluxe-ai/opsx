"""Replay compatibility for PBH identity gate (`workflow.patched`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from activities import (
    ActivityConfig,
    configure_activities,
    ensure_cursor_session,
    execute_commit_materialize,
    execute_project_bug_hunt,
    execute_project_bug_hunt_orch,
    reset_worker_control_plane_identity_for_tests,
    verify_control_plane_worker_identity,
)
from config import TEMPORAL_TASK_QUEUE
from control_plane_code_identity import compute_control_plane_code_identity
from models import REVIEW_CONTRACT_INVALID
from pbh_legacy_workflow_for_replay import LegacyProjectBugHuntWorkflowPreTerminalEnvelope
from pbh_workflow_test_helpers import start_pbh_with_admission
from workflow import ProjectBugHuntWorkflow

_HUNT_ACTIVITIES = [
    verify_control_plane_worker_identity,
    ensure_cursor_session,
    execute_project_bug_hunt,
    execute_project_bug_hunt_orch,
    execute_commit_materialize,
]


def test_pbh_identity_gate_is_patch_guarded_in_workflow_source():
    """Legacy histories without the patch marker skip verify on replay."""
    source = Path(__file__).resolve().parents[1] / "workflow.py"
    text = source.read_text(encoding="utf-8")
    assert 'workflow.patched("pbh-control-plane-identity-gate")' in text
    assert 'workflow.patched("pbh-terminal-failure-envelope")' in text
    assert "verify_control_plane_worker_identity" in text


@pytest.mark.asyncio
async def test_pbh_completed_history_replays_with_current_workflow_code():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="project-bug-hunt-one-repair-cycle",
            lane="project-bug-hunt",
            base="main",
            head="HEAD",
            change="UNRESOLVED_CHANGE",
        )
    )
    reset_worker_control_plane_identity_for_tests()
    workflow_input = {
        "mode": "mock",
        "repo_path": ".",
        "mock_scenario": "project-bug-hunt-one-repair-cycle",
        "lane": "project-bug-hunt",
        "base": "main",
        "head": "HEAD",
        "change": "UNRESOLVED_CHANGE",
        "control_plane_code_identity": compute_control_plane_code_identity(),
        "expected_candidate_sha": "HEAD",
    }
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[ProjectBugHuntWorkflow],
            activities=_HUNT_ACTIVITIES,
        ):
            handle = await start_pbh_with_admission(
                env.client,
                workflow_input,
                workflow_id="pbh-replay-source",
                task_queue=TEMPORAL_TASK_QUEUE,
            )
            await handle.result()
            history = await handle.fetch_history()
        replayer = Replayer(workflows=[ProjectBugHuntWorkflow])
        await replayer.replay_workflow(history)


@pytest.mark.asyncio
async def test_pbh_legacy_contract_invalid_completed_history_replays_with_new_code():
    """Histories that COMPLETED with REVIEW_CONTRACT_INVALID must replay without FAIL remap."""
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="project-bug-hunt-contract-invalid",
            lane="project-bug-hunt",
            base="main",
            head="HEAD",
            change="UNRESOLVED_CHANGE",
        )
    )
    reset_worker_control_plane_identity_for_tests()
    workflow_input = {
        "mode": "mock",
        "repo_path": ".",
        "mock_scenario": "project-bug-hunt-contract-invalid",
        "lane": "project-bug-hunt",
        "base": "main",
        "head": "HEAD",
        "change": "UNRESOLVED_CHANGE",
    }
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[LegacyProjectBugHuntWorkflowPreTerminalEnvelope],
            activities=[
                ensure_cursor_session,
                execute_project_bug_hunt,
                execute_project_bug_hunt_orch,
                execute_commit_materialize,
            ],
        ):
            handle = await env.client.start_workflow(
                LegacyProjectBugHuntWorkflowPreTerminalEnvelope.run,
                workflow_input,
                id="pbh-legacy-contract-invalid",
                task_queue=TEMPORAL_TASK_QUEUE,
            )
            legacy_result = await handle.result()
            history = await handle.fetch_history()
    assert legacy_result["status"] == REVIEW_CONTRACT_INVALID
    assert legacy_result.get("temporal_status") is None
    replayer = Replayer(workflows=[ProjectBugHuntWorkflow])
    await replayer.replay_workflow(history)
