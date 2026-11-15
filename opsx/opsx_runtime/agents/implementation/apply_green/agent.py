"""Runtime-агент apply-green hop (команда ``/opsx-apply-green``) в split-apply pipeline."""

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
from agents.implementation.apply_green.prompt.get_prompt import get_prompt
from agents.implementation.apply_green.schemas import ApplyGreenAnswer
from agents.implementation.green_hop_product_edit import (
    green_hop_allows_product_edit,
    green_hop_tools_for_slice,
)
from agents.implementation.hop_lane_status import merge_split_apply_hop_result
from agents.implementation.machine_gate_helpers import sdk_terminal
from agents.state import GraphState

# Slice GREEN hop: scoped read/edit + run_pytest; без grep/glob/ls/shell (repo_root_scan).
_APPLY_GREEN_TOOLS = ("read", "edit", "run_pytest")
from agents.verify.impl_verify.preflight import (
    collect_apply_machine_context,
    load_change_artifact_bundle,
)
from workflows.implementation.apply_hop_workflow_receipts import apply_green_workflow_receipt
from check_workflow_receipt import CheckWorkflowReceiptStatus
from workflows.repo_paths import normalize_workspace_path, resolve_opsx_repository_root  # noqa: E402
from workflows.repair.schemas import FixEvidence

_EXECUTION_ROUTE = "/opsx-apply-green"
_STREAM_AGENT = "apply_green"
_PATCH_PREFIX = "apply_green"


class ApplyGreenAgent(ABCAgent):
    """Исполнитель apply-green hop: GREEN receipt и repair-loop под slice."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить apply-green hop и вернуть patch state для следующего узла графа."""
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

        hop_receipt = apply_green_workflow_receipt(state)
        if hop_receipt.check_name is not None:
            receipt_status = hop_receipt.check(state)
            if receipt_status is CheckWorkflowReceiptStatus.MATCH:
                skip_answer = ApplyGreenAnswer(
                    status="NO_CHANGE",
                    tasks_summary="receipt MATCH skip",
                    gap_closure_summary="no hop",
                )
                return merge_split_apply_hop_result(
                    {
                        f"{_PATCH_PREFIX}_status": "NO_CHANGE",
                        f"{_PATCH_PREFIX}_marker": "⏭️ APPLY_NO_CHANGE",
                        f"{_PATCH_PREFIX}_report_text": f"Apply green skip (receipt MATCH) `{change_id}`",
                        f"{_PATCH_PREFIX}_answer": skip_answer.model_dump(mode="json"),
                    },
                    "NO_CHANGE",
                    hop_state=state,
                )

        artifacts = load_change_artifact_bundle(change_id, repo_root=Path(repo))
        machine_context = collect_apply_machine_context(change_id, repo_root=Path(repo))
        slice_id = str(state.get("implementation_slice_id") or "").strip()
        prompt = get_prompt(
            repository_path=workspace,
            change_id=change_id,
            impl_verify_answer=answer_raw,
            artifact_bundle=artifacts,
            machine_context=machine_context,
        )
        if slice_id and not green_hop_allows_product_edit(
            change_id,
            slice_id,
            repo_root=repo_path,
        ):
            prompt = (
                f"{prompt.rstrip()}\n\n"
                "## ADOPTION GREEN (verify-only)\n\n"
                "This slice: RED ALREADY_SATISFIED (observation) + IMPLEMENTATION_ADOPTED "
                "with no product paths. Tools: **read** and **run_pytest** only — "
                "**no edit** on `src/**` or `tests/**`. "
                "If frozen RED fails, set status=BLOCKED and name the failing test in "
                "`remaining_gaps`; do not patch production here.\n"
            )
        hop_tools = green_hop_tools_for_slice(
            _APPLY_GREEN_TOOLS,
            change_id,
            slice_id,
            repo_root=repo_path,
        )
        transport = adapter_transport_state(
            state,
            stream_log=state.get("apply_stream_log") or state.get("stream_log"),
            stream_label=compose_agent_stream_label(state, _STREAM_AGENT),
        )
        outcome = await self._adapter.run(
            transport,
            prompt=prompt,
            agent_key="apply_green",
            tools=hop_tools,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=ApplyGreenAnswer,
            format_instructions=schema_format_reminder(ApplyGreenAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = ApplyGreenAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        effective_status = answer.status
        if (
            effective_status in ("APPLY_COMPLETE", "APPLY_PARTIAL")
            and not sdk_terminal(outcome)
        ):
            effective_status = "BLOCKED"
        elif (
            effective_status in ("APPLY_COMPLETE", "APPLY_PARTIAL")
            and hop_receipt.check_name is not None
        ):
            _ = hop_receipt.establish_patch(state)

        marker = {
            "APPLY_COMPLETE": "✅ APPLY_COMPLETE",
            "APPLY_PARTIAL": "⚠️ APPLY_PARTIAL",
            "BLOCKED": "❌ APPLY_BLOCKED",
            "NO_CHANGE": "⏭️ APPLY_NO_CHANGE",
            "ESCALATE_ARBITER": "↗️ ESCALATE_ARBITER",
        }[effective_status]

        fix: dict[str, Any] | None = None
        if effective_status in ("APPLY_COMPLETE", "APPLY_PARTIAL") and answer.files_changed:
            fix = FixEvidence(
                status="FIXED",
                summary=answer.gap_closure_summary,
                files_changed=list(answer.files_changed),
                execution_route=_EXECUTION_ROUTE,
                notes=answer.tasks_summary,
            ).model_dump(mode="json")

        patch: dict[str, Any] = {
            f"{_PATCH_PREFIX}_status": effective_status,
            f"{_PATCH_PREFIX}_marker": marker,
            f"{_PATCH_PREFIX}_report_text": (
                f"Apply green hop for change `{change_id}`\n"
                f"Status: {effective_status}\n"
                f"Summary: {answer.gap_closure_summary}\n"
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
        }
        return merge_split_apply_hop_result(patch, effective_status, hop_state=state)
