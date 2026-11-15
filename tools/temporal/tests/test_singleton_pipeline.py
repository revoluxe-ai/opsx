"""Hard singleton: one live Temporal PP of a given identity."""

from pathlib import Path

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities import (
    ActivityConfig,
    configure_activities,
    ensure_cursor_session,
    execute_commit_materialize,
    execute_security_repair,
    execute_security_review,
)
from config import TEMPORAL_TASK_QUEUE
from singleton import PipelineLock, pipeline_lock_path, slug_token, stable_workflow_id
from workflow import SecurityReviewWorkflow


def test_stable_workflow_id_is_deterministic_and_lane_scoped():
    assert stable_workflow_id("security-review-poc", "real") == "security-review-poc-real"
    assert stable_workflow_id("bugbot-repair-poc", "real") == "bugbot-repair-poc-real"
    assert stable_workflow_id(
        "verification-execution-poc",
        "real",
        change="cross-device-novel-save-refresh",
    ) == "verification-execution-poc-real-cross-device-novel-save-refresh"


def test_stable_workflow_id_slugs_unsafe_change():
    wid = stable_workflow_id("project-bug-hunt-poc", "mock", change="Foo Bar/../x")
    assert " " not in wid
    assert ".." not in wid
    assert slug_token("Foo Bar/../x") in wid


def test_pipeline_lock_rejects_second_holder(tmp_path: Path):
    path = pipeline_lock_path(tmp_path, "security")
    first = PipelineLock(path)
    second = PipelineLock(path)
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_pipeline_lock_change_is_distinct_from_lane(tmp_path: Path):
    a = PipelineLock(pipeline_lock_path(tmp_path, "verification", change="one"))
    b = PipelineLock(pipeline_lock_path(tmp_path, "verification", change="two"))
    assert a.acquire() is True
    assert b.acquire() is True
    a.release()
    b.release()


@pytest.mark.asyncio
async def test_temporal_rejects_second_identical_security_workflow():
    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=".",
            mock_scenario="security-scenario1",
        )
    )
    workflow_id = stable_workflow_id("security-review-poc", "mock")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[SecurityReviewWorkflow],
            activities=[
                ensure_cursor_session,
                execute_security_review,
                execute_security_repair,
                execute_commit_materialize,
            ],
        ):
            handle = await env.client.start_workflow(
                SecurityReviewWorkflow.run,
                {
                    "mode": "mock",
                    "repo_path": ".",
                    "mock_scenario": "security-scenario1",
                },
                id=workflow_id,
                task_queue=TEMPORAL_TASK_QUEUE,
            )
            with pytest.raises(WorkflowAlreadyStartedError):
                await env.client.start_workflow(
                    SecurityReviewWorkflow.run,
                    {
                        "mode": "mock",
                        "repo_path": ".",
                        "mock_scenario": "security-scenario1",
                    },
                    id=workflow_id,
                    task_queue=TEMPORAL_TASK_QUEUE,
                )
            result = await handle.result()

    assert result["status"] == "SECURITY_CLEAN"
