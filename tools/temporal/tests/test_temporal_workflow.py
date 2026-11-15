import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities import (
    ActivityConfig,
    configure_activities,
    ensure_cursor_session,
    execute_bugbot_review,
    execute_commit_materialize,
    execute_opsx_repair,
    execute_project_bug_hunt,
    execute_project_bug_hunt_orch,
    execute_security_repair,
    execute_security_review,
)
from config import TEMPORAL_TASK_QUEUE
from workflow import BugbotRepairWorkflow, ProjectBugHuntWorkflow, SecurityReviewWorkflow

_BUGBOT_ACTIVITIES = [
    ensure_cursor_session,
    execute_bugbot_review,
    execute_opsx_repair,
    execute_commit_materialize,
]
_SECURITY_ACTIVITIES = [
    ensure_cursor_session,
    execute_security_review,
    execute_security_repair,
    execute_commit_materialize,
]
_HUNT_ACTIVITIES = [
    ensure_cursor_session,
    execute_project_bug_hunt,
    execute_project_bug_hunt_orch,
    execute_commit_materialize,
]


@pytest.mark.asyncio
async def test_temporal_workflow_exposes_granular_activities():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="scenario1",
        )
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[BugbotRepairWorkflow],
            activities=_BUGBOT_ACTIVITIES,
        ):
            result = await env.client.execute_workflow(
                BugbotRepairWorkflow.run,
                {
                    "mode": "mock",
                    "repo_path": ".",
                    "mock_scenario": "scenario1",
                },
                id="workflow-granular-test",
                task_queue=TEMPORAL_TASK_QUEUE,
            )

    assert result["status"] == "BUGBOT_CLEAN"
    assert result["repair_rounds"] == 2
    assert result["review_runs"] == 3
    assert result["last_materialize"]["ok"] is True
    assert result["last_materialize"]["status"] == "COMPLETE_NOOP"


@pytest.mark.asyncio
async def test_temporal_workflow_requires_final_review_after_tenth_repair():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="scenario2",
        )
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[BugbotRepairWorkflow],
            activities=_BUGBOT_ACTIVITIES,
        ):
            result = await env.client.execute_workflow(
                BugbotRepairWorkflow.run,
                {
                    "mode": "mock",
                    "repo_path": ".",
                    "mock_scenario": "scenario2",
                },
                id="workflow-final-review-after-tenth-repair",
                task_queue=TEMPORAL_TASK_QUEUE,
            )

    assert result["status"] == "MAX_ROUNDS_REACHED"
    assert result["repair_rounds"] == 10
    assert result["review_runs"] == 11


@pytest.mark.asyncio
async def test_security_workflow_uses_security_activities():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="security-scenario1",
        )
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[SecurityReviewWorkflow],
            activities=_SECURITY_ACTIVITIES,
        ):
            result = await env.client.execute_workflow(
                SecurityReviewWorkflow.run,
                {
                    "mode": "mock",
                    "repo_path": ".",
                    "mock_scenario": "security-scenario1",
                },
                id="workflow-security-granular-test",
                task_queue=TEMPORAL_TASK_QUEUE,
            )

    assert result["status"] == "SECURITY_CLEAN"
    assert result["repair_rounds"] == 2
    assert result["review_runs"] == 3


@pytest.mark.asyncio
async def test_project_bug_hunt_workflow_uses_hunt_activities():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="project-bug-hunt-scenario1",
            lane="project-bug-hunt",
            base="stage",
            head="save-game-progress",
            change="cross-device-novel-save-refresh",
        )
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[ProjectBugHuntWorkflow],
            activities=_HUNT_ACTIVITIES,
        ):
            result = await env.client.execute_workflow(
                ProjectBugHuntWorkflow.run,
                {
                    "mode": "mock",
                    "repo_path": ".",
                    "mock_scenario": "project-bug-hunt-scenario1",
                    "lane": "project-bug-hunt",
                    "base": "stage",
                    "head": "save-game-progress",
                    "change": "cross-device-novel-save-refresh",
                },
                id="workflow-project-bug-hunt-granular-test",
                task_queue=TEMPORAL_TASK_QUEUE,
            )

    assert result["status"] == "HUNT_CLEAN"
    assert result["repair_rounds"] == 2
    assert result["review_runs"] == 3
