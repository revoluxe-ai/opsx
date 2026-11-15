"""Runtime-агент apply-hop (``/opsx-apply``) после IMPL_GAPS, scenario FAIL или archive NO_GO."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.agent_tools.parent_ctx import enrich_transport_with_agent_tools
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.abc_agent import ABCAgent
from agents.implementation.apply.prompt.get_prompt import get_prompt
from agents.implementation.apply.cover_asset_apply_gate import gate_apply_cover_bundle
from agents.implementation.apply.schemas import ApplyAnswer
from agents.verify.scenario_coverage_verify.evidence_contract import input_snapshot
from agents.repair.helpers import MUTATING

# Verify apply-hop: scoped read + edit; без grep/glob/ls/shell (shell → repo_root_scan).
_APPLY_TOOLS = tuple(t for t in MUTATING if t not in ("grep", "glob", "ls", "shell"))
from agents.state import GraphState
from agents.verify.graph_validation import validate_apply_patch
from agents.verify.impl_verify.preflight import (
    collect_apply_machine_context,
    load_change_artifact_bundle,
)
from workflows.repo_paths import normalize_workspace_path, resolve_opsx_repository_root  # noqa: E402
from workflows.repair.schemas import FixEvidence
from workflows.verify.green_loop import (
    VERIFY_APPLY_GAP_SOURCE_ARCHIVE,
    VERIFY_APPLY_GAP_SOURCE_IMPL,
    VERIFY_APPLY_GAP_SOURCE_SCENARIO,
)


def resolve_apply_gap_context(state: GraphState) -> tuple[str, dict[str, Any]]:
    """Выбрать ``gap_source`` и JSON payload для apply-prompt (приоритет ``verify_apply_gap_source``)."""
    impl_answer: dict[str, Any] = (
        state.get("answer") if isinstance(state.get("answer"), dict) else {}
    )
    scenario_answer: dict[str, Any] = (
        state.get("scenario_coverage_answer")
        if isinstance(state.get("scenario_coverage_answer"), dict)
        else {}
    )
    gap_hint = str(state.get("verify_apply_gap_source") or "").strip()
    mode = str(state.get("mode") or "")
    verdict = str(state.get("verdict") or "")
    scenario_verdict = str(state.get("scenario_coverage_verdict") or "")

    if gap_hint == VERIFY_APPLY_GAP_SOURCE_ARCHIVE or (
        mode == "archive" and verdict != "GO"
    ):
        return VERIFY_APPLY_GAP_SOURCE_ARCHIVE, impl_answer
    if gap_hint == VERIFY_APPLY_GAP_SOURCE_SCENARIO or (
        scenario_verdict == "SCENARIO_TEST_COVERAGE_FAIL"
    ):
        return VERIFY_APPLY_GAP_SOURCE_SCENARIO, scenario_answer
    if gap_hint == VERIFY_APPLY_GAP_SOURCE_IMPL or verdict == "IMPL_GAPS":
        return VERIFY_APPLY_GAP_SOURCE_IMPL, impl_answer
    if gap_hint:
        return gap_hint, impl_answer if gap_hint != VERIFY_APPLY_GAP_SOURCE_SCENARIO else scenario_answer
    return VERIFY_APPLY_GAP_SOURCE_IMPL, impl_answer


def scenario_coverage_apply_input_error(
    state: GraphState, *, repo_root: Path, change_id: str,
) -> str | None:
    """Prevent writes from a stale or unbound scenario review."""
    prior = state.get("scenario_coverage_source_snapshot")
    answer = state.get("scenario_coverage_answer")
    if not isinstance(prior, dict) or not isinstance(answer, dict):
        return "SCENARIO_APPLY_UNBOUND: scenario audit snapshot or answer missing"
    digest = str(prior.get("source_digest") or "")
    if not digest or digest != str(answer.get("source_digest") or ""):
        return "SCENARIO_APPLY_UNBOUND: source identity missing or mismatched"
    try:
        current = input_snapshot(repo_root, change_id)
    except (OSError, ValueError) as exc:
        return f"SCENARIO_APPLY_INPUT_INVALID: {type(exc).__name__}: {exc}"
    if current["source_digest"] != digest:
        return (
            "SCENARIO_APPLY_STALE: audited source changed after coverage review; "
            "re-audit before any mutating Apply"
        )
    return None


class ApplyAgent(ABCAgent):
    """Исполнитель apply-hop: IMPL, scenario coverage или archive NO_GO gaps → commit."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить apply-hop и вернуть patch для commit + receipt полей."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo_path = resolve_opsx_repository_root(Path(str(state["repository_path"])))
        repo = str(repo_path)
        workspace = str(
            normalize_workspace_path(state.get("workspace"), repo_path),
        )

        impl_answer_raw = state.get("answer")
        if not isinstance(impl_answer_raw, dict):
            impl_answer_raw = {}

        scenario_answer_raw = state.get("scenario_coverage_answer")
        if not isinstance(scenario_answer_raw, dict):
            scenario_answer_raw = {}

        gap_source, gaps_for_prompt = resolve_apply_gap_context(state)
        archive_answer_raw = (
            impl_answer_raw if gap_source == VERIFY_APPLY_GAP_SOURCE_ARCHIVE else {}
        )

        if gap_source == VERIFY_APPLY_GAP_SOURCE_SCENARIO:
            input_error = scenario_coverage_apply_input_error(
                state, repo_root=repo_path, change_id=change_id,
            )
            if input_error:
                blocked = ApplyAnswer(
                    status="BLOCKED",
                    tasks_summary="No changes: audited source identity is stale or missing.",
                    gap_closure_summary=input_error,
                    files_changed=[],
                    remaining_gaps=[input_error],
                    addressed_gap_ids=[],
                    unverified_tests=[],
                    next_command="STOP_OWNER_ATTENTION",
                    local_hop_copy_block=(
                        f"Change: {change_id}\n"
                        f"Status: BLOCKED\n"
                        f"Reason: {input_error}\n"
                        "Recovery: reconcile source, then start a fresh audit."
                    ),
                )
                patch = {
                    "apply_status": "BLOCKED",
                    "apply_marker": "⏸ APPLY_BLOCKED_STALE_SCENARIO_INPUT",
                    "apply_report_text": input_error,
                    "apply_answer": blocked.model_dump(mode="json"),
                    "verify_finding_fix": None,
                    "verify_apply_gap_source": "scenario_coverage_verify",
                    "verify_apply_block_reason": input_error,
                }
                validate_apply_patch(patch)
                return patch

        artifacts = load_change_artifact_bundle(change_id, repo_root=Path(repo))
        machine_context = collect_apply_machine_context(change_id, repo_root=Path(repo))
        prompt = await get_prompt(
            repository_path=workspace,
            change_id=change_id,
            impl_verify_answer=impl_answer_raw,
            scenario_coverage_answer=scenario_answer_raw,
            archive_verify_answer=archive_answer_raw,
            gap_source=gap_source,
            gaps_for_prompt=gaps_for_prompt,
            artifact_bundle=artifacts,
            machine_context=machine_context,
        )
        transport = enrich_transport_with_agent_tools(
            adapter_transport_state(
                state,
                stream_log=state.get("apply_stream_log") or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "apply"),
            ),
            self._adapter,
            parent_agent_key="apply",
        )
        outcome = await self._adapter.run(
            transport,
            prompt=prompt,
            agent_key="apply",
            tools=_APPLY_TOOLS,
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=ApplyAnswer,
            format_instructions=schema_format_reminder(ApplyAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = ApplyAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        cover_gate = gate_apply_cover_bundle(
            repo_path,
            files_changed=list(answer.files_changed),
        )
        if cover_gate and answer.status == "APPLY_COMPLETE":
            outstanding = list(answer.remaining_gaps)
            outstanding.append(cover_gate)
            answer = answer.model_copy(
                update={"status": "APPLY_PARTIAL", "remaining_gaps": outstanding},
            )
        if gap_source == VERIFY_APPLY_GAP_SOURCE_SCENARIO:
            incoming = gaps_for_prompt.get("gaps") or []
            expected_ids = {
                str(item.get("scenario_id") or "").strip()
                for item in incoming
                if isinstance(item, dict) and str(item.get("scenario_id") or "").strip()
            }
            addressed = set(answer.addressed_gap_ids)
            omitted = sorted(expected_ids - addressed)
            if answer.status == "APPLY_COMPLETE" and (
                omitted or answer.remaining_gaps or answer.unverified_tests
            ):
                outstanding = list(answer.remaining_gaps)
                outstanding.extend(f"UNADDRESSED_SCENARIO: {sid}" for sid in omitted)
                outstanding.extend(f"UNVERIFIED_TEST: {test}" for test in answer.unverified_tests)
                answer = answer.model_copy(
                    update={"status": "APPLY_PARTIAL", "remaining_gaps": outstanding},
                )

        marker = {
            "APPLY_COMPLETE": "✅ APPLY_COMPLETE",
            "APPLY_PARTIAL": "⚠️ APPLY_PARTIAL",
            "BLOCKED": "❌ APPLY_BLOCKED",
            "NO_CHANGE": "⏭️ APPLY_NO_CHANGE",
        }[answer.status]

        fix: dict[str, Any] | None = None
        if answer.status in ("APPLY_COMPLETE", "APPLY_PARTIAL") and answer.files_changed:
            fix = FixEvidence(
                status="FIXED",
                summary=answer.gap_closure_summary,
                files_changed=list(answer.files_changed),
                execution_route="/opsx-apply",
                notes=answer.tasks_summary,
            ).model_dump(mode="json")

        patch: dict[str, Any] = {
            "apply_status": answer.status,
            "apply_marker": marker,
            "apply_report_text": (
                f"Apply hop for change `{change_id}`\n"
                f"Status: {answer.status}\n"
                f"Summary: {answer.gap_closure_summary}\n"
                f"Files: {', '.join(answer.files_changed) or 'none'}\n"
                f"{marker}"
            ),
            "apply_answer": answer.model_dump(mode="json"),
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
        validate_apply_patch(patch)
        return patch
