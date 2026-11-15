"""Runtime-агент Group Error Agent: группировка findings и diagnostic handoff."""

from __future__ import annotations

from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.abc_agent import ABCAgent
from agents.state import GraphState
from agents.verify.group_error.diagnostics_input_integrity import (
    input_integrity_from_gate_diagnostics,
)
from agents.verify.group_error.execution_integrity import (
    assess_execution_integrity,
)
from agents.verify.group_error.gate_only_triage import (
    build_gate_only_group_error_result,
    deterministic_group_error_handoff_patch,
    findings_from_gate_state,
    resolve_group_error_llm_enabled,
)
from agents.verify.group_error.input_pack import (
    build_verification_package,
    package_json,
)
from agents.verify.group_error.prompt.get_prompt import get_prompt
from agents.verify.group_error.schemas import (
    InputIntegrity,
    GroupErrorResult,
)
from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.validation import (
    recompute_triage_summary,
    validate_group_error_triage_invariants,
)

GROUP_ERROR_PARTITION_MISMATCH_AFTER_GATES = (
    "GROUP_ERROR_PARTITION_MISMATCH_AFTER_GATES"
)
GROUP_ERROR_FALLBACK_OWNER_PREFIX = (
    "GROUP_ERROR_FALLBACK: LLM partition invalid; using gate-only singleton triage"
)


def _diagnostics_dict(state: GraphState) -> dict[str, Any] | None:
    diagnostics = state.get("quality_gate_diagnostics")
    if isinstance(diagnostics, dict):
        return diagnostics
    return None


# Backward-compatible alias for tests patching ``agent._findings_from_gate_state``.
_findings_from_gate_state = findings_from_gate_state


class GroupErrorAgent(ABCAgent):
    """Read-only triage: группировка gate findings без tools и без repair queue."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Построить triage ``analysis_result`` из ``quality_gate_*`` и ответа LLM."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)
        execution_id = state.get("quality_gates_run_id") or state.get("run_id")
        diagnostics = _diagnostics_dict(state)
        from bug_log.context import active_journal
        from bug_log.mint import register_gate_findings

        gate_findings = findings_from_gate_state(state)
        journal = getattr(self, "_bug_journal", None) or active_journal()
        registered = await register_gate_findings(journal, gate_findings)
        bug_ids = {**dict(state.get("bug_ids") or {}), **registered}
        deterministic_integrity = input_integrity_from_gate_diagnostics(diagnostics)
        execution_key = str(execution_id) if execution_id else None

        package = build_verification_package(
            change_id=change_id,
            repository_path=repo,
            state=dict(state),
            execution_id=execution_key,
        )
        integrity_verdict, integrity_issues = assess_execution_integrity(
            state=dict(state),
            package=package,
        )
        if integrity_verdict == "BLOCKED":
            analysis = build_gate_only_group_error_result(
                change_id=change_id,
                execution_id=execution_key,
                state=state,
                input_integrity=InputIntegrity(
                    status="INVALID",
                    issues=integrity_issues,
                ),
                identity_status="PARTIAL",
            )
            return deterministic_group_error_handoff_patch(
                analysis,
                execution_status="skipped_integrity",
                bug_ids=bug_ids,
            )

        if deterministic_integrity.status != "COMPLETE":
            analysis = build_gate_only_group_error_result(
                change_id=change_id,
                execution_id=execution_key,
                state=state,
                input_integrity=deterministic_integrity,
                identity_status="PARTIAL",
            )
            return deterministic_group_error_handoff_patch(
                analysis,
                execution_status="skipped_input_integrity",
                bug_ids=bug_ids,
            )

        if not resolve_group_error_llm_enabled(state):
            analysis = build_gate_only_group_error_result(
                change_id=change_id,
                execution_id=execution_key,
                state=state,
                input_integrity=deterministic_integrity,
            )
            return deterministic_group_error_handoff_patch(
                analysis,
                execution_status="deterministic_gate_only",
                bug_ids=bug_ids,
            )

        prompt = get_prompt(verification_package_json=package_json(package))

        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("group_error_stream_log")
                or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "group_error"),
            ),
            prompt=prompt,
            agent_key="group_error",
            tools=(),
            resume_agent_id=resume_for_role(state, "group_error_agent_id"),
            payload_model=GroupErrorResult,
            format_instructions=schema_format_reminder(GroupErrorResult),
            max_validation_attempts=self.validation_max_attempts,
        )
        analysis = outcome.parsed  # type: ignore[assignment]
        if diagnostics is not None:
            analysis = analysis.model_copy(
                update={
                    "findings": findings_from_gate_state(state),
                    "input_integrity": deterministic_integrity,
                },
            )
        analysis = normalize_group_error_result(analysis)
        try:
            validate_group_error_triage_invariants(analysis)
        except ValueError:
            fallback = build_gate_only_group_error_result(
                change_id=change_id,
                execution_id=execution_key,
                state=state,
                input_integrity=deterministic_integrity,
            )
            return {
                **deterministic_group_error_handoff_patch(
                    fallback,
                    execution_status="fallback_partition",
                    owner_attention=(
                        f"{GROUP_ERROR_FALLBACK_OWNER_PREFIX}; "
                        f"{GROUP_ERROR_PARTITION_MISMATCH_AFTER_GATES}"
                    ),
                ),
                **agent_session_patch(
                    state,
                    agent_id=outcome.agent_id,
                    agent_id_key="group_error_agent_id",
                ),
                "group_error_run_id": outcome.run_id,
                "group_error_usage": outcome.usage,
                "group_error_cost": outcome.cost,
                "group_error_cost_status": outcome.cost_status,
                "group_error_billing_error": outcome.billing_error,
                "group_error_usage_source": outcome.usage_source,
                "group_error_validation_attempts": outcome.validation_attempts,
                "bug_ids": bug_ids,
            }
        analysis = recompute_triage_summary(analysis)
        result = outcome
        return {
            "analysis_result": analysis.model_dump(mode="json"),
            **agent_session_patch(
                state,
                agent_id=result.agent_id,
                agent_id_key="group_error_agent_id",
            ),
            "group_error_run_id": result.run_id,
            "group_error_execution_status": result.status,
            "group_error_usage": result.usage,
            "group_error_cost": result.cost,
            "group_error_cost_status": result.cost_status,
            "group_error_billing_error": result.billing_error,
            "group_error_usage_source": result.usage_source,
            "group_error_validation_attempts": outcome.validation_attempts,
            "bug_ids": bug_ids,
        }
