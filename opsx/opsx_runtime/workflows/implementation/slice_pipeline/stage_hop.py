"""Один stage-hop: детерминированная проверка рецепта, затем SDK агент."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, NoReturn, TypeVar

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")

from agents.implementation.hop_lane_status import (
    graph_dispatch_answer,
    merge_split_apply_hop_result,
)
from agents.state import GraphState
from workflows.implementation.routing import ImplementationLaneTerminalError
from workflows.implementation.routing_contract import (
    RUN_STATUS_FAILED,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)
from workflows.implementation.slice_receipt_dispatch import (
    SliceReceiptValidationAnswer,
    evaluate_for_hop,
    persist_after_successful_hop,
)
from workflows.implementation.hop_worktree import (
    capture_hop_worktree_baseline,
    collect_hop_watch_paths,
    git_diff_paths_since,
    hop_snapshot_changed_paths,
    parse_hop_worktree_snapshot,
)
from workflows.implementation.stage_receipt_writer import (
    StageReceiptWriteResult,
    StageReceiptWriter,
)
from workflows.repo_paths import resolve_opsx_repository_root
from workflows.implementation.red_operative_validation import (
    slice_red_json_path,
    validate_red_operative,
)
from workflows.implementation.slice_receipt import receipt_json_path

def _hop_check_result_summary(result: Any) -> str:
    """Краткое описание результата hop-проверки для structured log."""
    valid = getattr(result, "valid", None)
    code = getattr(result, "code", None)
    ok = getattr(result, "ok", None)
    if valid is not None:
        return f"valid={valid} code={code}"
    if ok is not None:
        return f"ok={ok}"
    return type(result).__name__


async def _run_blocking_hop_check(
    operation: str,
    *,
    change_id: str,
    slice_id: str,
    stage: str,
    fn: Callable[..., _T],
    fn_args: tuple[Any, ...] = (),
    fn_kwargs: dict[str, Any] | None = None,
) -> _T:
    """Синхронную длительную hop-проверку выполнить в worker thread с логом длительности."""
    started = time.perf_counter()
    _LOGGER.info(
        "slice_hop_check_start change_id=%s slice_id=%s stage=%s operation=%s",
        change_id,
        slice_id,
        stage,
        operation,
    )
    result = await asyncio.to_thread(fn, *fn_args, **(fn_kwargs or {}))
    duration_s = time.perf_counter() - started
    _LOGGER.info(
        (
            "slice_hop_check_done change_id=%s slice_id=%s stage=%s "
            "operation=%s duration_s=%.3f result=%s"
        ),
        change_id,
        slice_id,
        stage,
        operation,
        duration_s,
        _hop_check_result_summary(result),
    )
    return result


_STAGE_TO_PREFIX: dict[str, str] = {
    STAGE_APPLY_SCAFFOLD: "apply_scaffold",
    STAGE_APPLY_RED: "apply_red",
    STAGE_APPLY_IMPLEMENT: "apply_implement",
    STAGE_APPLY_GREEN: "apply_green",
}

_SUCCESS_HOP_STATUSES = frozenset({"APPLY_COMPLETE", "APPLY_PARTIAL", "NO_CHANGE"})

_RED_ORACLE_DIRECTIVE = (
    "Update frozen test @opsx-red-behavior-value to match the "
    "authoritative obligation when behavior is satisfied, or "
    "change the test so pytest fails with RED_PROVEN evidence."
)

_RED_MATERIALIZE_RETRY_CODES = frozenset(
    {"RED_ORACLE_BEHAVIOR_MARKER_GAP", "RECEIPT_PUBLISH_MISSING"},
)

_GREEN_VERIFY_RETRY_CODES = frozenset(
    {"RED_EVIDENCE_NOT_PASS", "GREEN_VERIFICATION_MISSING"},
)

_GREEN_VERIFY_DIRECTIVE = (
    "Runtime replay of frozen RED test_commands did not exit 0. "
    "The model status APPLY_COMPLETE is not evidence. Fix production inside "
    "this slice's write surfaces and rerun each frozen tests/ node with "
    "run_pytest until exit 0. Do not edit frozen tests and do not write GREEN.json."
)


def _red_hop_claims_success(
    lane_hop_status: str,
    hop_answer: dict[str, Any] | None,
) -> bool:
    """Typed answer или lane status говорят об успешном RED hop без on-disk receipt."""
    if lane_hop_status in _SUCCESS_HOP_STATUSES:
        return True
    return (
        isinstance(hop_answer, dict)
        and str(hop_answer.get("status") or "").strip() in _SUCCESS_HOP_STATUSES
    )


def _resolve_materialize_hop_status(
    stage: str,
    hop_status: str,
    hop_answer: dict[str, Any] | None,
) -> str:
    """Статус для ``record_hop_decision``.

    ApplyRedAgent может demote lane ``apply_red_status`` в ``BLOCKED`` при
    нестандартном SDK status, оставляя ``apply_red_answer.status`` успешным —
    materialize обязан следовать typed answer, иначе RED.json не публикуется.
    """
    materialize = hop_status
    if stage != STAGE_APPLY_RED or not isinstance(hop_answer, dict):
        return materialize
    answer_status = str(hop_answer.get("status") or "").strip()
    if materialize not in _SUCCESS_HOP_STATUSES and answer_status in _SUCCESS_HOP_STATUSES:
        return answer_status
    return materialize


def _red_first_materialize_refusal_patch(
    state: GraphState,
    result: dict[str, Any],
    *,
    lane_hop_status: str,
    hop_state: GraphState,
    recovery_code: str,
) -> dict[str, Any]:
    """Первый отказ materialize RED: RUNNING + один повтор hop."""
    retry_count = int(state.get("implementation_red_oracle_retry_count") or 0) + 1
    merged = merge_split_apply_hop_result(
        result,
        lane_hop_status,
        hop_state=hop_state,
    )
    merged["implementation_receipt_gate"] = "AGENT"
    merged["implementation_last_hop"] = STAGE_APPLY_RED
    merged["implementation_red_oracle_retry_count"] = retry_count
    if retry_count > 1:
        merged["implementation_status"] = RUN_STATUS_PAUSED
        merged["implementation_recovery_code"] = recovery_code
        return merged
    merged["implementation_status"] = RUN_STATUS_RUNNING
    merged["implementation_arbiter_target_stage"] = STAGE_APPLY_RED
    merged["implementation_arbiter_directive"] = _RED_ORACLE_DIRECTIVE
    merged["implementation_stage"] = STAGE_APPLY_RED
    return merged


def _green_verify_refusal_patch(
    state: GraphState,
    result: dict[str, Any],
    *,
    lane_hop_status: str,
    hop_state: GraphState,
    recovery_code: str,
    detail: str,
) -> dict[str, Any]:
    """Первый отказ replay GREEN: RUNNING + один повтор hop с хвостом pytest."""
    retry_count = int(state.get("implementation_green_verify_retry_count") or 0) + 1
    evidence = (detail or recovery_code).strip()
    merged = merge_split_apply_hop_result(
        result,
        lane_hop_status,
        hop_state=hop_state,
    )
    merged["implementation_receipt_gate"] = "AGENT"
    merged["implementation_last_hop"] = STAGE_APPLY_GREEN
    merged["implementation_green_verify_retry_count"] = retry_count
    merged["implementation_machine_gate_errors"] = [
        {
            "code": recovery_code,
            "field": "red_executions",
            "message": evidence[:4000],
        },
    ]
    if retry_count > 1:
        merged["implementation_status"] = RUN_STATUS_PAUSED
        merged["implementation_recovery_code"] = recovery_code
        return merged
    merged["implementation_status"] = RUN_STATUS_RUNNING
    merged["implementation_stage"] = STAGE_APPLY_GREEN
    merged["implementation_arbiter_target_stage"] = STAGE_APPLY_GREEN
    merged["implementation_arbiter_directive"] = (
        f"{_GREEN_VERIFY_DIRECTIVE} Evidence: {evidence[:2000]}"
    )
    merged["implementation_recovery_code"] = recovery_code
    return merged


def _green_requests_design_redo(result: dict[str, Any]) -> bool:
    answer = result.get("apply_green_answer")
    if not isinstance(answer, dict):
        return False
    gaps = answer.get("remaining_gaps")
    if isinstance(gaps, list):
        for gap in gaps:
            text = str(gap or "")
            if text.startswith("DESIGN_REDO:") or text.startswith("/opsx-apply-design"):
                return True
    recovery = str(result.get("implementation_recovery_code") or "")
    return recovery.startswith("DESIGN")


def _apply_design_redo_patch(
    state: GraphState,
    patch: dict[str, Any],
) -> dict[str, Any]:
    count = int(state.get("implementation_design_redo_count") or 0)
    if count >= 1:
        raise ImplementationLaneTerminalError(
            "DESIGN_REDO_LIMIT: green requested design redo more than once",
        )
    merged = dict(patch)
    merged["implementation_force_design_hop"] = True
    merged["implementation_design_redo_count"] = count + 1
    merged["implementation_status"] = RUN_STATUS_RUNNING
    return merged


def _dispatch_json(
    validation: Any,
    *,
    stage: str,
    slice_id: str,
    gate: Literal["PRE_AGENT", "POST_AGENT", "SKIP", "SKIP_MIGRATE"],
) -> dict[str, Any]:
    payload = SliceReceiptValidationAnswer.from_validation(
        validation,
        stage=stage,
        slice_id=slice_id,
        gate=gate,
    )
    return payload.model_dump(mode="json")


def _raise_post_validate_failure(
    *,
    stage: str,
    slice_id: str,
    post: Any,
) -> NoReturn:
    code = str(post.code or "RECEIPT_INVALID")
    raise ImplementationLaneTerminalError(
        "RECEIPT_POST_VALIDATE: "
        f"receipt must be valid after hop for {slice_id}/{stage}; gate failed ({code})",
    )


async def run_slice_stage_hop(
    state: GraphState,
    *,
    agent: Any,
    stage: str,
) -> dict[str, Any]:
    """Проверить рецепт без модели; при невалидном — ``agent.run``; затем записать и перепроверить."""
    change_id = str(state.get("change_id") or "").strip()
    slice_id = str(state.get("implementation_slice_id") or "").strip()
    if not change_id or not slice_id:
        raise ValueError("change_id and implementation_slice_id are required")
    repo_root = resolve_opsx_repository_root(Path(str(state["repository_path"])))
    prefix = _STAGE_TO_PREFIX[stage]
    force_arbiter_stage = (
        str(state.get("implementation_arbiter_target_stage") or "").strip() == stage
        and bool(str(state.get("implementation_arbiter_directive") or "").strip())
    )
    evaluation = await _run_blocking_hop_check(
        "evaluate_for_hop",
        change_id=change_id,
        slice_id=slice_id,
        stage=stage,
        fn=evaluate_for_hop,
        fn_args=(change_id, slice_id, stage),
        fn_kwargs={"repo_root": repo_root, "state": state},
    )
    skip_gate: Literal["SKIP", "SKIP_MIGRATE"] = (
        "SKIP_MIGRATE" if evaluation.backfilled_digest else "SKIP"
    )
    pre_json = _dispatch_json(
        evaluation,
        stage=stage,
        slice_id=slice_id,
        gate=skip_gate if evaluation.valid else "PRE_AGENT",
    )
    if evaluation.valid and not force_arbiter_stage:
        skip_patch: dict[str, Any] = {
            f"{prefix}_status": "NO_CHANGE",
            "implementation_status": RUN_STATUS_RUNNING,
            "implementation_stage": stage,
            "implementation_last_hop": stage,
            "implementation_receipt_gate": "SKIP",
            "implementation_slice_receipt_validation": pre_json,
        }
        if stage == STAGE_APPLY_RED and int(state.get("implementation_red_oracle_retry_count") or 0) > 0:
            skip_patch["implementation_red_oracle_retry_count"] = 0
        if stage == STAGE_APPLY_GREEN and int(
            state.get("implementation_green_verify_retry_count") or 0
        ) > 0:
            skip_patch["implementation_green_verify_retry_count"] = 0
            skip_patch["implementation_machine_gate_errors"] = None
        return skip_patch

    watch_paths = await asyncio.to_thread(
        collect_hop_watch_paths,
        change_id,
        slice_id,
        stage,
        repo_root=repo_root,
    )
    hop_baseline = await asyncio.to_thread(
        capture_hop_worktree_baseline,
        repo_root,
        watch_paths,
    )
    hop_state: GraphState = {
        **state,
        "mode": stage.lower(),
        "implementation_stage": stage,
        "implementation_slice_receipt_validation": pre_json,
        "implementation_hop_worktree_baseline": hop_baseline,
        "answer": graph_dispatch_answer(
            {**state, "implementation_slice_receipt_validation": pre_json},
            slice_id,
        ),
    }
    if (
        stage == STAGE_APPLY_RED
        and not evaluation.valid
        and evaluation.code == "RECEIPT_NOT_ACCEPTED"
    ):
        red_path = slice_red_json_path(change_id, repo_root=repo_root, slice_id=slice_id)
        gate = await _run_blocking_hop_check(
            "validate_red_operative",
            change_id=change_id,
            slice_id=slice_id,
            stage=stage,
            fn=validate_red_operative,
            fn_args=(red_path,),
            fn_kwargs={
                "repo_root": repo_root,
                "change_id": change_id,
                "slice_id": slice_id,
            },
        )
        hop_state["apply_red_operative_validation"] = {
            "ok": gate.ok,
            "exit_code": gate.exit_code,
            "status": gate.status,
            "errors": gate.errors,
        }
    result = await agent.run(hop_state)
    hop_status = str(result.get(f"{prefix}_status") or "").strip()
    answer_key = f"{prefix}_answer"
    hop_answer = result.get(answer_key)
    if not isinstance(hop_answer, dict):
        hop_answer = result.get("answer") if isinstance(result.get("answer"), dict) else None
    materialize_hop_status = _resolve_materialize_hop_status(stage, hop_status, hop_answer)
    post_json = pre_json
    if materialize_hop_status in _SUCCESS_HOP_STATUSES or materialize_hop_status == "BLOCKED":
        model_claimed_paths: list[str] = []
        if isinstance(hop_answer, dict):
            raw_fc = hop_answer.get("files_changed")
            if isinstance(raw_fc, list):
                model_claimed_paths = [
                    str(p).replace("\\", "/")
                    for p in raw_fc
                    if isinstance(p, str) and p.strip()
                ]
        if parse_hop_worktree_snapshot(hop_baseline) is not None:
            files_changed = await asyncio.to_thread(
                hop_snapshot_changed_paths,
                repo_root,
                hop_baseline,
                candidate_paths=model_claimed_paths,
            )
        else:
            files_changed = await asyncio.to_thread(
                git_diff_paths_since,
                repo_root,
                hop_baseline,
            )
        decision: dict[str, Any] = {
            "stage": stage,
            "slice_id": slice_id,
            "hop_status": materialize_hop_status,
            "hop_worktree_baseline": hop_baseline,
            "hop_files_changed": files_changed,
        }
        if isinstance(hop_answer, dict):
            decision["hop_answer"] = hop_answer
        if materialize_hop_status == "BLOCKED":
            decision["blockers"] = StageReceiptWriter._blockers_from_hop_answer(hop_answer)
        materialize = await asyncio.to_thread(
            StageReceiptWriter.record_hop_decision,
            change_id,
            repo_root=repo_root,
            decision=decision,
        )
        if (
            materialize.ok
            and stage == STAGE_APPLY_RED
            and not receipt_json_path(
                change_id,
                slice_id,
                STAGE_APPLY_RED,
                repo_root=repo_root,
            ).is_file()
        ):
            materialize = StageReceiptWriteResult(
                False,
                "RECEIPT_PUBLISH_MISSING",
                None,
                stage,
            )
        if not materialize.ok:
            refuse_code = str(materialize.code or "")
            if stage == STAGE_APPLY_RED and refuse_code in _RED_MATERIALIZE_RETRY_CODES:
                recovery = (
                    refuse_code
                    if refuse_code == "RED_ORACLE_BEHAVIOR_MARKER_GAP"
                    else "RECEIPT_PUBLISH_MISSING"
                )
                return _red_first_materialize_refusal_patch(
                    state,
                    result,
                    lane_hop_status=materialize_hop_status,
                    hop_state=hop_state,
                    recovery_code=recovery,
                )
            if stage == STAGE_APPLY_GREEN and refuse_code in _GREEN_VERIFY_RETRY_CODES:
                return _green_verify_refusal_patch(
                    state,
                    result,
                    lane_hop_status=materialize_hop_status,
                    hop_state=hop_state,
                    recovery_code=refuse_code,
                    detail=str(materialize.detail or ""),
                )
            raise ImplementationLaneTerminalError(
                "RECEIPT_MATERIALIZE: "
                f"{slice_id}/{stage} failed ({materialize.code or 'unknown'})",
            )
    lane_hop_status = (
        materialize_hop_status
        if materialize_hop_status in _SUCCESS_HOP_STATUSES
        else hop_status
    )
    if lane_hop_status in _SUCCESS_HOP_STATUSES:
        try:
            post_eval = await _run_blocking_hop_check(
                "persist_after_successful_hop",
                change_id=change_id,
                slice_id=slice_id,
                stage=stage,
                fn=persist_after_successful_hop,
                fn_args=(change_id, slice_id, stage),
                fn_kwargs={"repo_root": repo_root, "state": state},
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ImplementationLaneTerminalError(
                f"RECEIPT_WRITE_FAILED: persist work_scope_digest failed for "
                f"{slice_id}/{stage}: {exc}",
            ) from exc
        if not post_eval.valid:
            _raise_post_validate_failure(stage=stage, slice_id=slice_id, post=post_eval)
        post_json = _dispatch_json(
            post_eval,
            stage=stage,
            slice_id=slice_id,
            gate="POST_AGENT",
        )
    merged = merge_split_apply_hop_result(result, lane_hop_status, hop_state=hop_state)
    merged["implementation_receipt_gate"] = "AGENT"
    merged["implementation_last_hop"] = stage
    merged["implementation_slice_receipt_validation"] = post_json
    if force_arbiter_stage:
        merged["implementation_arbiter_directive"] = None
        merged["implementation_arbiter_target_stage"] = None
        merged["implementation_arbiter_directed_design_hop"] = False
    if stage == STAGE_APPLY_GREEN and _green_requests_design_redo(merged):
        return _apply_design_redo_patch(state, merged)
    demoted_blocked = (
        hop_status == "BLOCKED"
        and isinstance(hop_answer, dict)
        and str(hop_answer.get("status") or "").strip() in _SUCCESS_HOP_STATUSES
    )
    if hop_status == "BLOCKED" and not demoted_blocked:
        blocked_status = str(merged.get("implementation_status") or "")
        if blocked_status not in (RUN_STATUS_PAUSED, RUN_STATUS_FAILED):
            merged["implementation_status"] = RUN_STATUS_PAUSED
    if stage == STAGE_APPLY_RED:
        red_path = receipt_json_path(
            change_id,
            slice_id,
            STAGE_APPLY_RED,
            repo_root=repo_root,
        )
        if not red_path.is_file() and _red_hop_claims_success(lane_hop_status, hop_answer):
            return _red_first_materialize_refusal_patch(
                state,
                result,
                lane_hop_status=lane_hop_status,
                hop_state=hop_state,
                recovery_code="RECEIPT_PUBLISH_MISSING",
            )
        if red_path.is_file():
            merged["implementation_red_oracle_retry_count"] = 0
    if stage == STAGE_APPLY_GREEN:
        merged["implementation_green_verify_retry_count"] = 0
        merged["implementation_machine_gate_errors"] = None
    return merged
