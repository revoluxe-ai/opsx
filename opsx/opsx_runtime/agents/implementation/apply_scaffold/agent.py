"""Runtime-агент apply-scaffold hop (стадия APPLY SCAFFOLD) в split-apply pipeline."""

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
from agents.implementation.apply_scaffold.prompt.get_prompt import get_prompt
from agents.implementation.apply_scaffold.tool_surface import (
    INPUT_SCOPE_INCOMPLETE,
    SCAFFOLD_TOOLS,
    search_in_scope,
    work_scope_from_design,
)
from agents.implementation.apply_scaffold.schemas import ApplyScaffoldAnswer
from agents.implementation.hop_lane_status import merge_split_apply_hop_result
from agents.implementation.machine_gate_helpers import sdk_terminal
from agents.state import GraphState
from agents.verify.impl_verify.preflight import (
    collect_apply_machine_context,
    load_change_artifact_bundle,
)
from workflows.implementation.receipt_loader import load_apply_design
from workflows.repair.schemas import FixEvidence
from workflows.repo_paths import normalize_workspace_path, resolve_opsx_repository_root  # noqa: E402

_EXECUTION_ROUTE = "APPLY_SCAFFOLD"
_STREAM_AGENT = "apply_scaffold"
_PATCH_PREFIX = "apply_scaffold"


def _incomplete_scope_patch(change_id: str, state: GraphState) -> dict[str, Any]:
    """Дизайн не дал work_scope: hop не идёт в SDK и не сканирует репозиторий."""
    answer = ApplyScaffoldAnswer(
        status="BLOCKED",
        tasks_summary=INPUT_SCOPE_INCOMPLETE,
        gap_closure_summary=(
            "APPLY-DESIGN has no work_scope paths for this slice. "
            "Discovery from the repository root is not available."
        ),
        remaining_gaps=[INPUT_SCOPE_INCOMPLETE],
    )
    patch: dict[str, Any] = {
        f"{_PATCH_PREFIX}_status": "BLOCKED",
        f"{_PATCH_PREFIX}_marker": "❌ APPLY_BLOCKED",
        f"{_PATCH_PREFIX}_report_text": (
            f"Apply scaffold hop for change `{change_id}`\n"
            f"Status: BLOCKED\n"
            f"{INPUT_SCOPE_INCOMPLETE}"
        ),
        f"{_PATCH_PREFIX}_answer": answer.model_dump(mode="json"),
        "execution_status": "cancelled",
    }
    return merge_split_apply_hop_result(patch, "BLOCKED", hop_state=state)


class ApplyScaffoldAgent(ABCAgent):
    """Исполнитель apply-scaffold hop: материализация scaffold/receipt под slice."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить apply-scaffold hop и вернуть patch state для следующего узла графа."""
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

        slice_id = str(state.get("implementation_slice_id") or answer_raw.get("slice_id") or "").strip()
        design = load_apply_design(change_id, repo_root=Path(repo))
        work_scope = work_scope_from_design(design, slice_id)
        if not work_scope:
            return _incomplete_scope_patch(change_id, state)
        scoped_files = search_in_scope(Path(repo), work_scope, "*")
        answer_raw = {
            **answer_raw,
            "work_scope": work_scope,
            "scoped_files": scoped_files,
        }
        artifacts = load_change_artifact_bundle(change_id, repo_root=Path(repo))
        machine_context = collect_apply_machine_context(change_id, repo_root=Path(repo))
        prompt = get_prompt(
            repository_path=workspace,
            change_id=change_id,
            impl_verify_answer=answer_raw,
            artifact_bundle=artifacts,
            machine_context=machine_context,
        )
        transport = adapter_transport_state(
            state,
            stream_log=state.get("apply_stream_log") or state.get("stream_log"),
            stream_label=compose_agent_stream_label(state, _STREAM_AGENT),
        )
        outcome = await self._adapter.run(
            transport,
            prompt=prompt,
            agent_key="apply_scaffold",
            tools=SCAFFOLD_TOOLS,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=ApplyScaffoldAnswer,
            format_instructions=schema_format_reminder(ApplyScaffoldAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = ApplyScaffoldAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        effective_status = answer.status
        if (
            effective_status in ("APPLY_COMPLETE", "APPLY_PARTIAL")
            and not sdk_terminal(outcome)
        ):
            effective_status = "BLOCKED"

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
                f"Apply scaffold hop for change `{change_id}`\n"
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
