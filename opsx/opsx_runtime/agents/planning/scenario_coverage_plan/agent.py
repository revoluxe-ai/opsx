"""Runtime planning hop: durable scenario-coverage plan до implementation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.abc_agent import ABCAgent
from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.common.planning_artifacts import inspect_plan_artifacts
from agents.common.scenario_files import materialize_scenario_files, reconcile_scenario_filenames
from agents.planning.scenario_coverage_plan.plan_receipt_writer import commit_plan_machine_receipt
from agents.planning.scenario_coverage_plan.prompt.get_prompt import get_prompt
from agents.verify.impl_verify.preflight import load_change_artifact_bundle
from agents.planning.scenario_coverage_plan.receipt import (
    PreserveAction,
    ScenarioCoveragePlanReceipt,
)
from agents.planning.scenario_coverage_plan.schemas import ScenarioCoveragePlanAnswer
from agents.repair.helpers import MUTATING
from agents.state import GraphState
from workflows.repo_paths import normalize_workspace_path, resolve_opsx_repository_root

_STREAM_AGENT = "scenario_coverage_plan"


class ScenarioCoveragePlanAgent(ABCAgent):
    """Пишет scenario-coverage plan; skip при preserve."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить planning hop или skip без SDK."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo_path = resolve_opsx_repository_root(Path(str(state["repository_path"])))
        workspace = str(normalize_workspace_path(state.get("workspace"), repo_path))
        receipt = ScenarioCoveragePlanReceipt(repo_path, change_id)
        decision = receipt.preserve_decision()
        if decision.action == PreserveAction.PRESERVED:
            ready = inspect_plan_artifacts(repo_path, change_id, phase="planning")
            return {
                "planning_readiness_status": "READY",
                "planning_readiness_result": ready,
            }
        if decision.action == PreserveAction.PAUSE:
            return {
                "planning_readiness_status": "PAUSED",
                "planning_readiness_result": {
                    "status": "BLOCKED",
                    "code": decision.code,
                    "detail": decision.detail,
                    "producer": decision.producer,
                },
            }
        artifact_bundle = load_change_artifact_bundle(
            change_id, repo_root=repo_path
        )
        prompt = await get_prompt(
            repository_path=workspace,
            change_id=change_id,
            artifact_bundle=artifact_bundle,
        )
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, _STREAM_AGENT),
            ),
            prompt=prompt,
            agent_key="scenario_coverage_plan",
            tools=MUTATING,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=ScenarioCoveragePlanAnswer,
            format_instructions=schema_format_reminder(ScenarioCoveragePlanAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = ScenarioCoveragePlanAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        if answer.status == "PLAN_READY":
            coverage = repo_path / "openspec" / "changes" / change_id / "scenario-coverage"
            _ = materialize_scenario_files(coverage)
            _ = reconcile_scenario_filenames(coverage)
            _ = commit_plan_machine_receipt(repo_path, change_id)
        ready = inspect_plan_artifacts(repo_path, change_id, phase="planning")
        status = "READY" if ready.get("status") == "READY" and answer.status == "PLAN_READY" else "PAUSED"
        return {
            **agent_session_patch(state, agent_id=outcome.agent_id, agent_id_key="agent_id"),
            "planning_readiness_status": status,
            "planning_readiness_result": ready,
        }
