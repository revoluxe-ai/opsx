"""Runtime-агент apply-design-review hop в split-apply pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.abc_agent import ABCAgent
from agents.implementation.apply_design_review.prompt.get_prompt import get_prompt
from agents.implementation.apply_design_review.schemas import ApplyDesignReviewAnswer
from agents.implementation.hop_lane_status import merge_split_apply_hop_result
from agents.implementation.machine_gate_helpers import (
    MACHINE_REPAIR_LIMIT,
    format_machine_errors,
    operative_gate_dict,
    sdk_terminal,
)
from agents.repair.helpers import MUTATING
from agents.state import GraphState
from agents.verify.impl_verify.preflight import (
    collect_apply_machine_context,
    load_change_artifact_bundle,
)
from workflows.implementation.routing_contract import RUN_STATUS_RUNNING
from workflows.implementation.slice_stage_operative_validation import (
    apply_design_review_json_path,
    validate_design_review_operative,
)
from workflows.repo_paths import normalize_workspace_path, resolve_opsx_repository_root  # noqa: E402
from workflows.repair.schemas import FixEvidence

_EXECUTION_ROUTE = "/opsx-apply-design-review"
_STREAM_AGENT = "apply_design_review"
_PATCH_PREFIX = "apply_design_review"
_GATE_FAIL_CODE = "DESIGN_REVIEW_OPERATIVE_GATE_FAILED"


class ApplyDesignReviewAgent(ABCAgent):
    """Исполнитель apply-design-review: семантический challenge READY APPLY DESIGN."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить apply-design-review hop и вернуть patch state для следующего узла графа."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo_path = resolve_opsx_repository_root(Path(str(state["repository_path"])))
        repo = str(repo_path)
        workspace = str(
            normalize_workspace_path(state.get("workspace"), repo_path),
        )

        answer_raw = state.get("answer")
        if not isinstance(answer_raw, dict):
            answer_raw = {}

        artifacts = load_change_artifact_bundle(change_id, repo_root=Path(repo))
        machine_context = collect_apply_machine_context(change_id, repo_root=Path(repo))
        prompt = get_prompt(
            repository_path=workspace,
            change_id=change_id,
            impl_verify_answer=answer_raw,
            artifact_bundle=artifacts,
            machine_context=machine_context,
        )
        machine_repair_count = int(state.get("implementation_hop_machine_repair_count") or 0)
        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("apply_stream_log") or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, _STREAM_AGENT),
            ),
            prompt=prompt,
            agent_key="apply_design_review",
            tools=MUTATING,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=ApplyDesignReviewAnswer,
            format_instructions=schema_format_reminder(ApplyDesignReviewAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = ApplyDesignReviewAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        effective_status = answer.status
        operative_validation: dict[str, Any] | None = None
        gate_statuses = frozenset({"APPLY_DESIGN_REVIEW_PASS", "REPAIRS_REQUIRED"})
        if effective_status in gate_statuses and sdk_terminal(outcome):

            def _run_gate() -> dict[str, Any]:
                path = apply_design_review_json_path(change_id, repo_root=repo_path)
                gate = validate_design_review_operative(path, repo_root=repo_path)
                return operative_gate_dict(
                    gate,
                    receipt_key="design_review_json",
                    repo_root=repo_path,
                )

            operative_validation = _run_gate()
            if (
                not operative_validation["ok"]
                and machine_repair_count < MACHINE_REPAIR_LIMIT
            ):
                repair_prompt = (
                    f"{prompt}\n\n## Machine DESIGN REVIEW validator FAIL (one corrective pass)\n"
                    f"{format_machine_errors(operative_validation['errors'])}\n"
                    "Fix APPLY-DESIGN-REVIEW JSON only; do not open opsx_tools.\n"
                )
                outcome = await self._adapter.run(
                    adapter_transport_state(
                        state,
                        stream_log=state.get("apply_stream_log") or state.get("stream_log"),
                        stream_label=compose_agent_stream_label(state, _STREAM_AGENT),
                    ),
                    prompt=repair_prompt,
                    agent_key="apply_design_review",
                    tools=MUTATING,
                    resume_agent_id=resume_for_role(state, "agent_id"),
                    payload_model=ApplyDesignReviewAnswer,
                    format_instructions=schema_format_reminder(ApplyDesignReviewAnswer),
                    max_validation_attempts=self.validation_max_attempts,
                )
                if sdk_terminal(outcome):
                    answer = ApplyDesignReviewAnswer.model_validate(
                        outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
                    )
                    effective_status = answer.status
                    machine_repair_count += 1
                    if effective_status in gate_statuses:
                        operative_validation = _run_gate()
                else:
                    effective_status = "REPAIRS_REQUIRED"
            if operative_validation is not None and not operative_validation["ok"]:
                effective_status = "REPAIRS_REQUIRED"

        marker = {
            "APPLY_DESIGN_REVIEW_PASS": "✅ APPLY_DESIGN_REVIEW_PASS",
            "REPAIRS_REQUIRED": "⚠️ REPAIRS_REQUIRED",
            "ESCALATE_ARBITER": "↗️ ESCALATE_ARBITER",
        }[effective_status]

        fix: dict[str, Any] | None = None
        if effective_status == "APPLY_DESIGN_REVIEW_PASS" and answer.files_changed:
            fix = FixEvidence(
                status="FIXED",
                summary=answer.findings_summary,
                files_changed=list(answer.files_changed),
                execution_route=_EXECUTION_ROUTE,
                notes=answer.review_summary,
            ).model_dump(mode="json")

        patch: dict[str, Any] = {
            f"{_PATCH_PREFIX}_status": effective_status,
            f"{_PATCH_PREFIX}_marker": marker,
            f"{_PATCH_PREFIX}_report_text": (
                f"Apply design review hop for change `{change_id}`\n"
                f"Status: {effective_status}\n"
                f"Summary: {answer.review_summary}\n"
                f"Findings: {answer.findings_summary}\n"
                f"Files: {', '.join(answer.files_changed) or 'none'}\n"
                f"{marker}"
            ),
            f"{_PATCH_PREFIX}_answer": answer.model_dump(mode="json"),
            "verify_finding_fix": fix,
            **agent_session_patch(state, agent_id=outcome.agent_id, agent_id_key="agent_id"),
            "run_id": outcome.run_id,
            "execution_status": outcome.status,
            "usage": outcome.usage,
            "cost": outcome.cost,
            "cost_status": outcome.cost_status,
            "billing_error": outcome.billing_error,
            "usage_source": outcome.usage_source,
            "validation_attempts": outcome.validation_attempts,
            "implementation_hop_machine_repair_count": machine_repair_count,
        }
        if operative_validation is not None:
            if operative_validation["ok"]:
                patch["implementation_machine_gate_errors"] = None
            else:
                patch["implementation_machine_gate_errors"] = operative_validation["errors"]
                patch["implementation_plan_reject_code"] = _GATE_FAIL_CODE
        if effective_status == "ESCALATE_ARBITER":
            return merge_split_apply_hop_result(patch, effective_status, hop_state=state)
        if effective_status == "REPAIRS_REQUIRED":
            code = (
                answer.blocker_codes[0] if answer.blocker_codes else "DESIGN_REVIEW_REPAIRS_REQUIRED"
            )
            # REPAIRS_REQUIRED → RUNNING. Следующую стадию выбирает plan-узел, не это поле.
            repairs_lane = {
                "implementation_status": RUN_STATUS_RUNNING,
                "implementation_recovery_code": code,
            }
            return merge_split_apply_hop_result(
                patch,
                effective_status,
                lane_override=repairs_lane,
                hop_state=state,
            )
        return merge_split_apply_hop_result(patch, effective_status, hop_state=state)
