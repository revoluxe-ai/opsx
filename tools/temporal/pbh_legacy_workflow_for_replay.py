"""Pre-PR ``ProjectBugHuntWorkflow`` body for Temporal history replay tests only."""

from __future__ import annotations

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from activities import WorkflowRunConfig
    from session_lifecycle_contract import hunt_heartbeat_timeout, hunt_start_to_close_timeout
    from workflow import _run_review_repair_loop


@workflow.defn(name="ProjectBugHuntWorkflow")
class LegacyProjectBugHuntWorkflowPreTerminalEnvelope:
    """Return loop payload directly (no terminal failure envelope)."""

    @workflow.run
    async def run(self, config: WorkflowRunConfig | dict) -> dict:
        if isinstance(config, dict):
            config = WorkflowRunConfig(**config)
        return await _run_review_repair_loop(
            config,
            review_activity="execute_project_bug_hunt",
            repair_activity="execute_project_bug_hunt_orch",
            clean_status="HUNT_CLEAN",
            review_start_to_close_timeout=hunt_start_to_close_timeout(),
            review_heartbeat_timeout=hunt_heartbeat_timeout(),
            repair_start_to_close_timeout=hunt_start_to_close_timeout(),
            repair_heartbeat_timeout=hunt_heartbeat_timeout(),
        )
