"""Runtime-агент apply-red hop (команда ``/opsx-apply-red``) в split-apply pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import (
    TERMINAL_SDK_STATUSES,
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.abc_agent import ABCAgent
from agents.implementation.apply_red.prompt.get_prompt import get_prompt
from agents.implementation.apply_red.schemas import ApplyRedAnswer
from agents.implementation.hop_lane_status import merge_split_apply_hop_result
from agents.state import GraphState

# Slice RED hop: scoped read/edit + run_pytest; без grep/glob/ls/shell (repo_root_scan).
_APPLY_RED_TOOLS = ("read", "edit", "run_pytest")
from agents.verify.impl_verify.preflight import (
    collect_apply_machine_context,
    load_change_artifact_bundle,
)
from workflows.repo_paths import normalize_workspace_path, resolve_opsx_repository_root  # noqa: E402
from workflows.repair.schemas import FixEvidence

_EXECUTION_ROUTE = "/opsx-apply-red"
_STREAM_AGENT = "apply_red"
_PATCH_PREFIX = "apply_red"
def _format_machine_errors(errors: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for entry in errors:
        code = str(entry.get("code") or "?")
        message = str(entry.get("message") or "")
        lines.append(f"- {code}: {message}")
    return "\n".join(lines)


def _sdk_terminal(outcome: Any) -> bool:
    """Проверить, что SDK hop завершился терминальным статусом (допустим машинный gate)."""
    return str(getattr(outcome, "status", "") or "").upper() in TERMINAL_SDK_STATUSES


def _operative_prompt_block(state: GraphState, base_prompt: str) -> str:
    """Добавить ошибки prior operative gate в prompt следующего RED hop."""
    operative = state.get("apply_red_operative_validation")
    if not isinstance(operative, dict) or operative.get("ok"):
        return base_prompt
    errors = operative.get("errors")
    if not isinstance(errors, list) or not errors:
        return base_prompt
    return (
        f"{base_prompt}\n\n## Prior machine RED validator FAIL\n"
        f"{_format_machine_errors([e for e in errors if isinstance(e, dict)])}\n"
    )


class ApplyRedAgent(ABCAgent):
    """Исполнитель apply-red hop: failing tests / RED receipt под slice."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить apply-red hop и вернуть patch state для следующего узла графа."""
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
        prompt = _operative_prompt_block(state, prompt)
        transport = adapter_transport_state(
            state,
            stream_log=state.get("apply_stream_log") or state.get("stream_log"),
            stream_label=compose_agent_stream_label(state, _STREAM_AGENT),
        )
        outcome = await self._adapter.run(
            transport,
            prompt=prompt,
            agent_key="apply_red",
            tools=_APPLY_RED_TOOLS,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=ApplyRedAnswer,
            format_instructions=schema_format_reminder(ApplyRedAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = ApplyRedAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        effective_status = answer.status
        if (
            effective_status in ("APPLY_COMPLETE", "APPLY_PARTIAL")
            and not _sdk_terminal(outcome)
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
                f"Apply red hop for change `{change_id}`\n"
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
