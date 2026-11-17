"""Runtime-агент apply-design hop в split-apply pipeline (authority в ``agents.implementation.prompt``)."""

from __future__ import annotations

import asyncio
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
from agents.implementation.apply_design.capability import (
    INPUT_SCOPE_INCOMPLETE,
    build_apply_design_manifest,
)
from agents.implementation.apply_design.tool_runtime import ApplyDesignToolRuntime
from agents.implementation.apply_design.prompt.get_prompt import get_prompt
from agents.implementation.apply_design.schemas import ApplyDesignAnswer
from agents.implementation.hop_lane_status import merge_split_apply_hop_result
from agents.state import GraphState
from agents.verify.impl_verify.preflight import (
    collect_apply_machine_context,
    load_change_artifact_bundle,
)
from workflows.implementation.apply_design_canon_reuse import (
    can_reuse_valid_apply_design_canon,
    design_submit_loop_blocked,
    patch_after_failed_submit,
    reset_design_submit_fail_tracking,
    unresolved_design_repair_intent,
)
from workflows.implementation.apply_design_hop_receipt import (
    decide_design_hop,
    design_hop_effective_status,
    load_design_bytes,
    maybe_recover_design_hop_receipt,
    write_hop_receipt,
)
from workflows.implementation.design_machine_validation import (
    accepted_slice_contract_anchor_text,
    evaluate_apply_design_dependent_snapshot_async,
    validate_apply_design_machine_async,
)
from workflows.repair.schemas import FixEvidence
from workflows.repo_paths import normalize_workspace_path, resolve_opsx_repository_root  # noqa: E402

_EXECUTION_ROUTE = "APPLY_DESIGN"
_STREAM_AGENT = "apply_design"
_PATCH_PREFIX = "apply_design"
_MACHINE_REPAIR_LIMIT = 1


def _loop_block_patch(
    change_id: str,
    state: GraphState,
    *,
    block_code: str,
) -> dict[str, Any]:
    """Diagnosable BLOCKED после двух одинаковых fail submit на одном каноне."""
    answer = ApplyDesignAnswer(
        status="BLOCKED",
        tasks_summary="apply-design submit loop exhausted",
        gap_closure_summary=(
            f"Two identical partial submits on the same APPLY-DESIGN revision; "
            f"code={block_code}"
        ),
        remaining_gaps=[block_code],
    )
    patch: dict[str, Any] = {
        f"{_PATCH_PREFIX}_status": "BLOCKED",
        f"{_PATCH_PREFIX}_marker": "❌ APPLY_BLOCKED",
        f"{_PATCH_PREFIX}_report_text": (
            f"Apply design hop for change `{change_id}`\nStatus: BLOCKED\n{block_code}"
        ),
        f"{_PATCH_PREFIX}_answer": answer.model_dump(mode="json"),
        "implementation_status": "PAUSED",
        "implementation_recovery_code": block_code,
        "implementation_plan_reject_code": block_code,
        "implementation_machine_gate_errors": [
            {"code": block_code, "message": block_code},
        ],
        "implementation_hop_machine_repair_count": int(
            state.get("implementation_hop_machine_repair_count") or 0
        ),
    }
    return merge_split_apply_hop_result(patch, "BLOCKED", hop_state=state)


def _incomplete_scope_patch(change_id: str, state: GraphState, gaps: tuple[str, ...]) -> dict[str, Any]:
    """Planning текущего change неполон — адаптер не вызывается."""
    answer = ApplyDesignAnswer(
        status="BLOCKED",
        tasks_summary=INPUT_SCOPE_INCOMPLETE,
        gap_closure_summary="Required planning inputs for this change are missing.",
        remaining_gaps=[INPUT_SCOPE_INCOMPLETE, *list(gaps)],
    )
    patch: dict[str, Any] = {
        f"{_PATCH_PREFIX}_status": "BLOCKED",
        f"{_PATCH_PREFIX}_marker": "❌ APPLY_BLOCKED",
        f"{_PATCH_PREFIX}_report_text": (
            f"Apply design hop for change `{change_id}`\n"
            f"Status: BLOCKED\n"
            f"{INPUT_SCOPE_INCOMPLETE}"
        ),
        f"{_PATCH_PREFIX}_answer": answer.model_dump(mode="json"),
        "execution_status": "cancelled",
    }
    return merge_split_apply_hop_result(patch, "BLOCKED", hop_state=state)


class ApplyDesignAgent(ABCAgent):
    """Исполнитель apply-design hop: freeze implementation-architecture под change."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить apply-design hop и вернуть patch state для следующего узла графа."""
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

        manifest = build_apply_design_manifest(change_id, repo_root=repo_path)
        if not manifest.planning_complete:
            return _incomplete_scope_patch(change_id, state, manifest.planning_gaps)

        design_gate = await validate_apply_design_machine_async(
            change_id, repo_root=repo_path
        )
        snapshot = await evaluate_apply_design_dependent_snapshot_async(
            change_id, repo_root=repo_path
        )
        artifact_present = snapshot.code != "APPLY_DESIGN_MISSING"
        repair_intent = unresolved_design_repair_intent(state)
        machine_repair_count = int(state.get("implementation_hop_machine_repair_count") or 0)
        submit_fail_streak = int(state.get("implementation_design_submit_fail_streak") or 0)

        likely_fail = str(state.get("implementation_design_submit_fail_code") or "") or None
        loop_blocked, loop_code = design_submit_loop_blocked(
            state,
            change_id=change_id,
            repo_root=repo_path,
            next_fail_code=likely_fail,
        )
        if loop_blocked and loop_code:
            return _loop_block_patch(change_id, state, block_code=loop_code)

        if can_reuse_valid_apply_design_canon(
            gate_ok=design_gate.ok,
            snapshot_ok=snapshot.ok,
            artifact_present=artifact_present,
            repair_intent=repair_intent,
            submit_fail_streak=submit_fail_streak,
        ):
            decision = decide_design_hop(
                submission=None,
                artifact_present=artifact_present,
                gate_ok=design_gate.ok,
                snapshot_ok=snapshot.ok,
            )
            _ = await maybe_recover_design_hop_receipt(
                decision=decision,
                change_id=change_id,
                repo_root=repo_path,
                gate_ok=design_gate.ok,
                snapshot_ok=snapshot.ok,
                attempt=machine_repair_count + 1,
            )
            if decision.status == "NO_CHANGE":
                design_body = await asyncio.to_thread(
                    load_design_bytes, change_id, repo_root=repo_path
                )
                if design_body is not None:
                    await write_hop_receipt(
                        change_id=change_id,
                        repo_root=repo_path,
                        design=design_body,
                        attempt=machine_repair_count + 1,
                    )
            answer = ApplyDesignAnswer(
                status="NO_CHANGE",
                tasks_summary="valid on-disk APPLY-DESIGN reused",
                gap_closure_summary="machine gate and snapshot pass; adapter call skipped",
            )
            patch: dict[str, Any] = {
                f"{_PATCH_PREFIX}_status": "NO_CHANGE",
                f"{_PATCH_PREFIX}_marker": "⏭️ APPLY_NO_CHANGE",
                f"{_PATCH_PREFIX}_report_text": (
                    f"Apply design hop for change `{change_id}`\n"
                    "Status: NO_CHANGE\nCanon reused without adapter call."
                ),
                f"{_PATCH_PREFIX}_answer": answer.model_dump(mode="json"),
                "implementation_machine_gate_errors": None,
                "implementation_plan_reject_code": None,
                "implementation_receipt_gate": "SKIP",
                "implementation_hop_machine_repair_count": machine_repair_count,
                **reset_design_submit_fail_tracking(change_id, repo_root=repo_path),
            }
            return merge_split_apply_hop_result(patch, "NO_CHANGE", hop_state=state)

        artifacts = load_change_artifact_bundle(change_id, repo_root=Path(repo))
        machine_context = collect_apply_machine_context(change_id, repo_root=Path(repo))
        prompt = await get_prompt(
            repository_path=workspace,
            change_id=change_id,
            impl_verify_answer=answer_raw,
            artifact_bundle=artifacts,
            machine_context=machine_context,
            base_digest=await manifest.submission_digest_async(),
            manifest=manifest,
        )
        transport = adapter_transport_state(
            state,
            stream_log=state.get("apply_stream_log") or state.get("stream_log"),
            stream_label=compose_agent_stream_label(state, _STREAM_AGENT),
        )
        tool_runtime = ApplyDesignToolRuntime(manifest)
        transport["capability_manifest"] = manifest
        transport["apply_design_tool_runtime"] = tool_runtime
        outcome = await self._adapter.run(
            transport,
            prompt=prompt,
            agent_key="apply_design",
            tools=(),
            resume_agent_id=resume_for_role(state, "agent_id"),
            payload_model=ApplyDesignAnswer,
            format_instructions=schema_format_reminder(ApplyDesignAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer = ApplyDesignAnswer.model_validate(
            outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
        )
        design_gate = await validate_apply_design_machine_async(
            change_id, repo_root=repo_path
        )
        snapshot = await evaluate_apply_design_dependent_snapshot_async(
            change_id, repo_root=repo_path
        )
        artifact_present = snapshot.code != "APPLY_DESIGN_MISSING"
        decision = decide_design_hop(
            submission=tool_runtime.last_submission,
            artifact_present=artifact_present,
            gate_ok=design_gate.ok,
            snapshot_ok=snapshot.ok,
        )
        effective_status = design_hop_effective_status(answer.status, decision.status)
        if (
            decision.status == "BLOCKED"
            and answer.status != "ESCALATE_ARBITER"
            and machine_repair_count < _MACHINE_REPAIR_LIMIT
        ):
                err_block = "\n".join(
                    f"- {e.get('code', '?')}: {e.get('message', '')}"
                    for e in design_gate.errors
                )
                anchor = accepted_slice_contract_anchor_text(change_id, repo_root=repo_path)
                drift = any(
                    str(e.get("code") or "") == "DESIGN_ACCEPTED_SLICE_CONTRACT_DRIFT"
                    for e in design_gate.errors
                )
                drift_block = (
                    f"\n## Accepted slice anchor (immutable)\n{anchor}\n"
                    if drift and anchor
                    else ""
                )
                current_revision = (
                    tool_runtime.current_revision_digest()
                    or await manifest.submission_digest_async()
                )
                repair_prompt = (
                    f"{prompt}\n\n## APPLY-DESIGN machine validator FAIL (one corrective pass)\n"
                    f"Current APPLY-DESIGN revision_digest (base_digest for submit): "
                    f"{current_revision!r}\n"
                    f"{err_block}\n"
                    f"{drift_block}"
                    "Classify slices: accepted (durable RED / IMPLEMENT / GREEN) vs future. "
                    "Do not change accepted slice red_test_targets, oracle bindings, or "
                    "required_behavior. For R3_FREEZE_CONFLICT on a future slice, assign new "
                    "test paths and bindings only on that slice. "
                    "After substantive DESIGN repair, APPLY-DESIGN-REVIEW must run again "
                    "(previous review is stale).\n"
                )
                outcome = await self._adapter.run(
                    transport,
                    prompt=repair_prompt,
                    agent_key="apply_design",
                    tools=(),
                    resume_agent_id=resume_for_role(state, "agent_id"),
                    payload_model=ApplyDesignAnswer,
                    format_instructions=schema_format_reminder(ApplyDesignAnswer),
                    max_validation_attempts=self.validation_max_attempts,
                )
                answer = ApplyDesignAnswer.model_validate(
                    outcome.parsed.model_dump(mode="json"),  # type: ignore[union-attr]
                )
                machine_repair_count += 1
                design_gate = await validate_apply_design_machine_async(
                    change_id, repo_root=repo_path
                )
                snapshot = await evaluate_apply_design_dependent_snapshot_async(
                    change_id, repo_root=repo_path
                )
                decision = decide_design_hop(
                    submission=tool_runtime.last_submission,
                    artifact_present=artifact_present,
                    gate_ok=design_gate.ok,
                    snapshot_ok=snapshot.ok,
                )
                effective_status = design_hop_effective_status(
                    answer.status, decision.status
                )
        submit_fail_code: str | None = None
        if tool_runtime.last_submission is not None and not tool_runtime.last_submission.ok:
            submit_fail_code = tool_runtime.last_submission.code
        loop_blocked, loop_code = design_submit_loop_blocked(
            state,
            change_id=change_id,
            repo_root=repo_path,
            next_fail_code=submit_fail_code,
        )
        if loop_blocked and loop_code:
            fail_patch = patch_after_failed_submit(
                state=state,
                change_id=change_id,
                repo_root=repo_path,
                fail_code=submit_fail_code,
            )
            blocked = _loop_block_patch(change_id, state, block_code=loop_code)
            return {**blocked, **fail_patch}
        _ = await maybe_recover_design_hop_receipt(
            decision=decision,
            change_id=change_id,
            repo_root=repo_path,
            gate_ok=design_gate.ok,
            snapshot_ok=snapshot.ok,
            attempt=machine_repair_count + 1,
        )
        if effective_status in ("APPLY_COMPLETE", "NO_CHANGE") and design_gate.ok and snapshot.ok:
            design_body = await asyncio.to_thread(
                load_design_bytes, change_id, repo_root=repo_path
            )
            if design_body is not None:
                await write_hop_receipt(
                    change_id=change_id,
                    repo_root=repo_path,
                    design=design_body,
                    attempt=machine_repair_count + 1,
                )
        elif decision.status == "BLOCKED":
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
                f"Apply design hop for change `{change_id}`\n"
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
            "implementation_hop_machine_repair_count": machine_repair_count,
        }
        if effective_status == "BLOCKED":
            errors = list(design_gate.errors)
            if decision.code and not errors:
                detail = decision.code
                if (
                    tool_runtime.last_submission is not None
                    and tool_runtime.last_submission.message
                ):
                    detail = tool_runtime.last_submission.message
                errors = [{"code": decision.code, "message": detail}]
            patch["implementation_machine_gate_errors"] = errors
            patch["implementation_plan_reject_code"] = decision.code or "DESIGN_MACHINE_GATE_FAILED"
            patch.update(
                patch_after_failed_submit(
                    state=state,
                    change_id=change_id,
                    repo_root=repo_path,
                    fail_code=submit_fail_code or decision.code,
                ),
            )
        elif effective_status == "NO_CHANGE":
            patch["implementation_machine_gate_errors"] = None
            patch["implementation_plan_reject_code"] = None
            patch["implementation_receipt_gate"] = "SKIP"
            patch.update(reset_design_submit_fail_tracking(change_id, repo_root=repo_path))
        elif design_gate.ok:
            patch["implementation_machine_gate_errors"] = None
            patch.update(reset_design_submit_fail_tracking(change_id, repo_root=repo_path))
        return merge_split_apply_hop_result(patch, effective_status, hop_state=state)
