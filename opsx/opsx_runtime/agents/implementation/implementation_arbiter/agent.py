"""Resume-агент implementation_arbiter: directive-only маршрутизация между implement hop-ами."""

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
from agents.implementation.implementation_arbiter.prompt.get_prompt import get_prompt
from agents.verify.impl_verify.preflight import load_change_artifact_bundle
from agents.implementation.implementation_arbiter.schemas import ArbiterDecision
from agents.repair.helpers import READ_ONLY
from agents.state import GraphState
from workflows.implementation.routing_contract import (
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_DESIGN,
)
from workflows.repo_paths import normalize_workspace_path, resolve_opsx_repository_root  # noqa: E402

_STREAM_AGENT = "implementation_arbiter"
_AGENT_ID_KEY = "implementation_arbiter_agent_id"
_DESIGN_TARGETS = frozenset({STAGE_APPLY_DESIGN})


class ImplementationArbiterAgent(ABCAgent):
    """Исполнитель arbiter hop: анализ и directive без записи в репозиторий."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить arbiter hop и вернуть patch с ``implementation_arbiter_*`` полями."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo_path = resolve_opsx_repository_root(Path(str(state["repository_path"])))
        workspace = str(normalize_workspace_path(state.get("workspace"), repo_path))

        raw_request = state.get("implementation_arbiter_request")
        arbiter_request = raw_request if isinstance(raw_request, dict) else None
        artifact_bundle = load_change_artifact_bundle(change_id, repo_root=repo_path)
        escalation_context = {
            "implementation_stage": state.get("implementation_stage"),
            "implementation_slice_id": state.get("implementation_slice_id"),
            "implementation_slices": state.get("implementation_slices"),
            "implementation_arbiter_calls": state.get("implementation_arbiter_calls"),
            "implementation_machine_gate_errors": state.get("implementation_machine_gate_errors"),
            "implementation_recovery_code": state.get("implementation_recovery_code"),
            "apply_design_answer": state.get("apply_design_answer"),
            "apply_scaffold_answer": state.get("apply_scaffold_answer"),
            "apply_red_answer": state.get("apply_red_answer"),
            "apply_implement_answer": state.get("apply_implement_answer"),
            "apply_green_answer": state.get("apply_green_answer"),
            "planning_artifact_excerpt": dict(artifact_bundle.get("artifacts") or {}),
        }
        prompt = await get_prompt(
            repository_path=workspace,
            change_id=change_id,
            arbiter_request=arbiter_request,
            escalation_context=escalation_context,
        )
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("apply_stream_log") or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, _STREAM_AGENT),
            ),
            prompt=prompt,
            agent_key="implementation_arbiter",
            tools=READ_ONLY,
            resume_agent_id=resume_for_role(state, _AGENT_ID_KEY),
            payload_model=ArbiterDecision,
            format_instructions=schema_format_reminder(ArbiterDecision),
            max_validation_attempts=self.validation_max_attempts,
        )
        decision = ArbiterDecision.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        if decision.close_without_target:
            return {
                "implementation_arbiter_answer": decision.model_dump(mode="json"),
                "implementation_arbiter_pending": False,
                "implementation_status": RUN_STATUS_PAUSED,
                "implementation_recovery_code": "ARBITER_OWNER_REQUIRED",
                **agent_session_patch(
                    state,
                    agent_id=outcome.agent_id,
                    agent_id_key=_AGENT_ID_KEY,
                ),
                "run_id": outcome.run_id,
                "execution_status": outcome.status,
            }

        if decision.target_stage is None:
            raise ValueError("ArbiterDecision.target_stage is required for routing patch")
        target_stage = decision.target_stage.strip().upper()
        patch: dict[str, Any] = {
            "implementation_arbiter_answer": decision.model_dump(mode="json"),
            "implementation_arbiter_directive": decision.directive,
            "implementation_arbiter_target_stage": target_stage,
            "implementation_arbiter_pending": False,
            "implementation_stage": target_stage,
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key=_AGENT_ID_KEY,
            ),
            "run_id": outcome.run_id,
            "execution_status": outcome.status,
            "usage": outcome.usage,
            "cost": outcome.cost,
            "cost_status": outcome.cost_status,
            "billing_error": outcome.billing_error,
            "usage_source": outcome.usage_source,
            "validation_attempts": outcome.validation_attempts,
        }
        if decision.target_slice_id:
            patch["implementation_slice_id"] = decision.target_slice_id.strip()
        if target_stage in _DESIGN_TARGETS:
            patch["implementation_force_design_hop"] = True
            patch["implementation_arbiter_directed_design_hop"] = True
            patch["apply_design_lane_complete"] = False
        patch["implementation_status"] = RUN_STATUS_RUNNING
        return patch
