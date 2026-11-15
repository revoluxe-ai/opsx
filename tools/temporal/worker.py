"""Temporal worker for the isolated review/repair POC (all lanes, one queue)."""

from __future__ import annotations

import asyncio
import atexit
import json
import os
from pathlib import Path

from temporalio.worker import Worker

from activities import (
    ActivityConfig,
    configure_activities,
    ensure_cursor_session,
    ensure_verification_context,
    execute_assigned_check,
    execute_bugbot_review,
    execute_commit_materialize,
    execute_next_check,
    execute_opsx_repair,
    execute_project_bug_hunt,
    execute_project_bug_hunt_orch,
    execute_security_repair,
    execute_security_review,
    parse_bugbot_result_activity,
    parse_opsx_bug_fix_result_activity,
)
from cursor_execution_activity import execute_cursor, reconcile_cursor
from config import TEMPORAL_TASK_QUEUE, apply_cursor_sdk_bridge_temp_dir
from control_plane_code_identity import compute_control_plane_code_identity
from model_config import AgentModelConfig
from temporal_client import connect_temporal_client
from workflow import (
    BugbotRepairWorkflow,
    CursorSdkExecutionWorkflow,
    ProjectBugHuntWorkflow,
    SecurityReviewWorkflow,
    VerificationExecutionWorkflow,
)


async def run_worker(
    mode: str,
    repo_path: str,
    mock_scenario: str | None = None,
    model_config_path: str | None = None,
) -> None:
    model_config = AgentModelConfig.load(model_config_path) if model_config_path else AgentModelConfig.load()
    print(json.dumps(model_config.summary(), ensure_ascii=False, indent=2))
    configure_activities(
        ActivityConfig(
            mode=mode,
            repo_path=repo_path,
            mock_scenario=mock_scenario,
            model_config_path=str(model_config.source_path) if model_config.source_path else None,
        )
    )
    client = await connect_temporal_client()
    worker = Worker(
        client,
        task_queue=TEMPORAL_TASK_QUEUE,
        workflows=[
            BugbotRepairWorkflow,
            SecurityReviewWorkflow,
            ProjectBugHuntWorkflow,
            VerificationExecutionWorkflow,
            CursorSdkExecutionWorkflow,
        ],
        activities=[
            ensure_cursor_session,
            ensure_verification_context,
            execute_cursor,
            reconcile_cursor,
            execute_bugbot_review,
            execute_opsx_repair,
            execute_commit_materialize,
            execute_security_review,
            execute_security_repair,
            execute_project_bug_hunt,
            execute_project_bug_hunt_orch,
            execute_next_check,
            execute_assigned_check,
            parse_bugbot_result_activity,
            parse_opsx_bug_fix_result_activity,
        ],
        max_concurrent_activities=4,
        max_concurrent_workflow_tasks=4,
    )
    from worker_poll_ready import clear_poll_ready, write_poll_ready

    attested_identity = compute_control_plane_code_identity()
    write_poll_ready(
        repo_path,
        pid=os.getpid(),
        task_queue=TEMPORAL_TASK_QUEUE,
        control_plane_code_identity=attested_identity,
    )
    atexit.register(clear_poll_ready, repo_path)
    print(f"Temporal worker listening on queue={TEMPORAL_TASK_QUEUE} mode={mode}")
    await worker.run()


def main() -> None:
    import argparse
    import os

    os.environ.setdefault("PYTHONUTF8", "1")
    apply_cursor_sdk_bridge_temp_dir()
    parser = argparse.ArgumentParser(description="Temporal worker for review/repair POC")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--mock-scenario", default="scenario1")
    parser.add_argument("--model-config", default=None)
    parser.add_argument("--review-tier", default=None)
    parser.add_argument("--repair-tier", default=None)
    args = parser.parse_args()
    model_config = args.model_config
    if args.review_tier or args.repair_tier:
        if not args.review_tier or not args.repair_tier:
            raise SystemExit("Both --review-tier and --repair-tier are required together")
        from model_config import write_step_overlay

        from run_store import durable_config_path

        dest = durable_config_path(args.repo)
        write_step_overlay(review=args.review_tier, repair=args.repair_tier, dest=dest)
        model_config = str(dest)

    asyncio.run(run_worker(args.mode, args.repo, args.mock_scenario, model_config))


if __name__ == "__main__":
    main()
