"""Test helpers for hermetic PBH Temporal workflows (importable via PYTHONPATH=.)."""

from __future__ import annotations

from typing import Any

from pbh_admission_binding import publish_admission_manifest_atomic
from workflow import ProjectBugHuntWorkflow


async def start_pbh_with_admission(
    client,
    workflow_input: dict[str, Any],
    *,
    workflow_id: str,
    task_queue: str,
):
    identity = workflow_input.get("control_plane_code_identity")
    head = workflow_input.get("head")
    repo = workflow_input.get("repo_path") or "."
    handle = await client.start_workflow(
        ProjectBugHuntWorkflow.run,
        workflow_input,
        id=workflow_id,
        task_queue=task_queue,
    )
    if identity and head:
        desc = await handle.describe()
        publish_admission_manifest_atomic(
            repo,
            workflow_id=workflow_id,
            workflow_run_id=desc.run_id,
            expected_candidate_sha=str(head),
            control_plane_code_identity=str(identity),
        )
    return handle


async def execute_pbh_with_admission(
    client,
    workflow_input: dict[str, Any],
    *,
    workflow_id: str,
    task_queue: str,
):
    handle = await start_pbh_with_admission(
        client,
        workflow_input,
        workflow_id=workflow_id,
        task_queue=task_queue,
    )
    return await handle.result()
