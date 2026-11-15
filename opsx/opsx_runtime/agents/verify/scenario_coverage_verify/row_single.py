"""One independent LLM-audit hop per pending frozen scenario; durable per-row checkpoints.

V1 runs a serial FIFO queue (one model audit at a time). A crashed/invalid LLM audit run cannot
erase previously committed per-row receipts. Only a deterministic gate assembles
the final plan verdict; individual PASS is never an aggregate PASS.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Mapping

from agents.abc_agent import AgentValidationTerminalError
from agents.adapters.abc_adapter import (
    TERMINAL_SDK_STATUSES,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.verify.scenario_coverage_verify.evidence_contract import (
    input_snapshot, review_proof_error,
)
from agents.verify.scenario_coverage_verify.delta_rows import ROW_CONTRACT
from agents.verify.scenario_coverage_verify.prompt.get_prompt import get_delta_prompt
from agents.verify.scenario_coverage_verify.progress import report as progress
from agents.verify.scenario_coverage_verify.row_receipts import (
    row_key, save_supported_row, write_observation,
)
from agents.verify.scenario_coverage_verify.review_semantics import verify_single_answer
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageRowReview,
    ScenarioCoverageVerifyAnswer,
    ScenarioCoverageVerifyModelAnswer,
    bind_scenario_coverage_authority,
)

_LOG = logging.getLogger(__name__)
_READ_TOOLS = ("read",)


def _provider_overload_reason(exc: Exception) -> str | None:
    """Only systemic model-transport failures open the queue circuit breaker."""
    name = type(exc).__name__.lower()
    detail = str(exc).lower()
    if (
        "ratelimit" in name or "rate_limit" in detail or "resource_exhausted" in detail
        or "get_models endpoint" in detail
    ):
        return "RATE_LIMIT"
    if (
        "502 bad gateway" in detail or "503 service unavailable" in detail
        or "504 gateway timeout" in detail or "bad gateway" in detail
    ):
        return "PROVIDER_GATEWAY"
    if (
        "remoteprotocolerror" in detail or "incomplete chunked read" in detail
        or "bridge request failed" in detail
        or "already has active run" in detail
        or "cannot wait on a detached running run" in detail
        or "sdk_run_reconciliation_required" in detail
        or "cursor_provider_overload_pause" in detail
    ):
        return "RUN_RECONCILIATION"
    return None


DEFAULT_MAX_CONCURRENT_SCENARIO_AGENTS = 1


def _row_stream_log(state: Mapping[str, Any], scenario_id: str) -> str | None:
    """Один общий run-log: scenario row дописывает в ``state['stream_log']``."""
    _ = scenario_id
    raw = state.get("stream_log")
    if not isinstance(raw, (str, Path)) or not str(raw).strip():
        return None
    execution = str(state.get("run_uuid") or "").strip()
    if not execution or not execution.replace("-", "").replace("_", "").isalnum():
        raise ValueError("SCENARIO_ROW_LOG_EXECUTION_UNBOUND")
    return str(raw).strip()


async def audit_pending_independently(
    *,
    adapter: Any,
    state: Mapping[str, Any],
    repo_root: Path,
    workspace: str,
    change_id: str,
    plan_ids: list[str],
    mapping: Mapping[str, str],
    plan_id: str,
    snapshot: Mapping[str, Any],
    evidence_ledger: list[dict[str, Any]],
    pending: list[str],
    reused: Mapping[str, ScenarioCoverageRowReview],
    row_digests: Mapping[str, str | None],
    execution_id: str,
    validation_attempts: int,
    shared_digest: str | None = None,
    reused_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    max_concurrency: int = DEFAULT_MAX_CONCURRENT_SCENARIO_AGENTS,
) -> tuple[ScenarioCoverageVerifyAnswer, Any, dict[str, dict[str, str]], dict[str, int]]:
    """Deterministic FIFO queue with exactly one active LLM audit.

    Each job commits its own atomic row proof before releasing a worker slot.
    No failed or blocked result becomes an aggregate PASS. An LLM-audit failure
    doesn't cancel already-running work; completed receipts survive restart.
    """
    if (
        not execution_id or not plan_id or not snapshot.get("source_digest")
        or not plan_ids or len(set(plan_ids)) != len(plan_ids)
        or set(reused) & set(pending)
        or set(reused) | set(pending) != set(plan_ids)
        or len(pending) != len(set(pending))
        or set(mapping) != set(plan_ids)
        or not isinstance(max_concurrency, int) or isinstance(max_concurrency, bool)
        or not 1 <= max_concurrency <= DEFAULT_MAX_CONCURRENT_SCENARIO_AGENTS
    ):
        raise ValueError("SCENARIO_ROW_RUN_UNBOUND")
    ledger_index = {item["scenario_id"]: item for item in evidence_ledger}
    if len(ledger_index) != len(plan_ids) or set(ledger_index) != set(plan_ids):
        raise ValueError("SCENARIO_ROW_PLAN_LEDGER_MISMATCH")

    # Order is the frozen-plan order, independently of LLM completion order.
    expected = [sid for sid in plan_ids if sid not in reused]
    if pending != expected:
        raise ValueError("SCENARIO_ROW_QUEUE_ORDER_MISMATCH")
    current_digest = str(snapshot["source_digest"])
    completed: dict[str, ScenarioCoverageRowReview] = {}
    origins: dict[str, dict[str, str]] = {}
    individual_gaps: dict[str, list[Any]] = {}
    blocked: dict[str, str] = {}
    # Rehydrate negative findings before scheduling. An audit with only
    # reused rows must still reproduce FAIL / UNKNOWN, never default to PASS.
    evidence = reused_evidence or {}
    if set(evidence) != {
        sid for sid, row in reused.items() if row.assessment != "SUPPORTED"
    }:
        raise ValueError("SCENARIO_ROW_CACHED_EVIDENCE_INCOMPLETE")
    for sid, record in evidence.items():
        if reused[sid].assessment == "GAP":
            raw_gaps = record.get("gaps")
            if not isinstance(raw_gaps, list) or not raw_gaps or any(
                not hasattr(g, "scenario_id") or g.scenario_id != sid for g in raw_gaps
            ):
                raise ValueError("SCENARIO_ROW_CACHED_GAPS_INVALID")
            individual_gaps[sid] = list(raw_gaps)
        elif reused[sid].assessment in ("CONTRACT_CONFLICT", "INSPECTION_BLOCKED"):
            reason = record.get("block_reason")
            if not isinstance(reason, str) or not reason.strip() or record.get("gaps"):
                raise ValueError("SCENARIO_ROW_CACHED_BLOCK_INVALID")
            blocked[sid] = reason
        else:
            raise ValueError("SCENARIO_ROW_CACHED_STATUS_INVALID")
    errors: dict[str, str] = {}
    outcomes: dict[str, Any] = {}
    stats = {"sdk_calls": 0, "observations_saved": 0, "supported_receipts_saved": 0}
    queue: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
    for i, sid in enumerate(pending, 1):
        queue.put_nowait((i, sid))
    source_unstable = False
    provider_paused = False
    overload_cause: str | None = None
    started = 0
    finished = 0
    active = 0

    progress(
        "QUEUE",
        change_id=change_id,
        total=len(plan_ids),
        reused=len(reused),
        model_queued=len(pending),
        concurrency=min(max_concurrency, len(pending)),
        execution_id=execution_id,
    )

    async def audit_one(index: int, sid: str) -> None:
        nonlocal source_unstable
        # Capture, conversation and log paths must never collide per audit run.
        stream_log = _row_stream_log(state, sid)
        prompt = get_delta_prompt(
            repository_path=workspace,
            change_id=change_id,
            plan_evidence_id=plan_id,
            source_snapshot=dict(snapshot),
            evidence_ledger=[ledger_index[sid]],
        )
        # Workers each start a *fresh* LLM audit (no shared agent dialogue).
        transport = adapter_transport_state(
            state, stream_log=stream_log,
            stream_label=compose_agent_stream_label(
                state, "scenario_coverage_verify",
                run_scope=f"scenario-{index:03d}",
            ),
            # run_dialogs and the transport inbox use this explicit agent slug.
            # The plain scenario_coverage_verify label would alias every
            # independent row into one canonical conversation directory.
            run_dialog_agent=f"scenario_coverage_verify-{row_key(sid)}",
        )
        # The common ABCAgent validation loop owns schema + semantic retries.
        # Only a finished, source-bound answer may be corrected in the same
        # LLM audit session; transport uncertainty must never replay a prompt.
        validated_by_agent = False

        def validate_row_payload(payload: Any) -> None:
            nonlocal validated_by_agent
            model_answer = ScenarioCoverageVerifyModelAnswer.model_validate(payload)
            answer = bind_scenario_coverage_authority(
                model_answer,
                plan_evidence_id=plan_id,
                source_digest=current_digest,
            )
            try:
                row = verify_single_answer(
                    answer=answer, scenario_id=sid,
                    mapped_node=mapping[sid], plan_id=plan_id,
                    source_digest=current_digest,
                )
            except ValueError as exc:
                if str(exc).startswith((
                    "SCENARIO_ROW_ID_MISMATCH",
                    "SCENARIO_ROW_MAPPING_CHANGED",
                )):
                    # Frozen source/identity mismatch is not a format correction.
                    raise AgentValidationTerminalError(str(exc)) from exc
                raise
            if row.assessment == "SUPPORTED":
                proof_error = review_proof_error(
                    repo_root, change_id, row, expected_node=mapping[sid],
                )
                if proof_error:
                    raise ValueError(f"SCENARIO_ROW_PROOF_INVALID: {proof_error}")
            validated_by_agent = True

        # Dispatch is logged at the actual adapter handoff, not when the row
        # was merely queued. Transport can still fail or retry internally.
        progress(
            "MODEL_DISPATCH",
            change_id=change_id, execution_id=execution_id,
            scenario_id=sid, index=index, pending_total=len(pending),
            reason="NO_FRESH_VALIDATED_ROW",
        )
        stats["sdk_calls"] += 1
        outcome = await adapter.run(
            transport,
            prompt=prompt,
            tools=_READ_TOOLS,
            agent_key="scenario_coverage_verify",
            resume_agent_id=None,
            payload_model=ScenarioCoverageVerifyModelAnswer,
            format_instructions=schema_format_reminder(ScenarioCoverageVerifyModelAnswer),
            max_validation_attempts=validation_attempts,
            post_parse_validate=validate_row_payload,
        )
        if (
            str(outcome.status).upper() not in TERMINAL_SDK_STATUSES
            or not outcome.run_id or not outcome.agent_id or outcome.parsed is None
        ):
            raise ValueError("SCENARIO_ROW_LLM_AUDIT_NOT_TERMINAL")
        # A custom/test adapter may omit the callback. Never accept a proof
        # solely because the adapter supplied a parsed object; validate once
        # locally when the common lifecycle was bypassed.
        if not validated_by_agent:
            validate_row_payload(outcome.parsed)
        answer = bind_scenario_coverage_authority(
            ScenarioCoverageVerifyModelAnswer.model_validate(outcome.parsed),
            plan_evidence_id=plan_id,
            source_digest=current_digest,
        )
        row = answer.row_reviews[0]
        latest = input_snapshot(repo_root, change_id)
        if latest["source_digest"] != current_digest:
            source_unstable = True
            raise ValueError("SCENARIO_ROW_SOURCE_CHANGED_DURING_HOP")
        if source_unstable:
            raise ValueError("SCENARIO_ROW_SOURCE_UNSTABLE")
        evidence_id = f"{outcome.run_id}:{outcome.agent_id}"
        write_observation(
            repo_root, change_id=change_id, row=row,
            row_digest=row_digests.get(sid),
            source_digest=current_digest, plan_evidence_id=plan_id,
            origin_execution_id=execution_id, evidence_id=evidence_id,
            shared_digest=shared_digest,
            row_identity_contract=ROW_CONTRACT,
            problem=(
                "; ".join(
                    f"{gap.gap_class}: {gap.evidence}" for gap in answer.gaps
                    if gap.scenario_id == sid
                )
                if row.assessment == "GAP" else answer.block_reason
            ),
            gap_class=(
                answer.gaps[0].gap_class if row.assessment == "GAP" else ""
            ),
            gaps=answer.gaps if row.assessment == "GAP" else [],
        )
        stats["observations_saved"] += 1
        if row.assessment == "SUPPORTED":
            if not save_supported_row(
                repo_root, change_id=change_id, row=row,
                mapping=mapping, row_digest=row_digests.get(sid),
                plan_evidence_id=plan_id, origin_execution_id=execution_id,
                evidence_id=evidence_id, row_identity_contract=ROW_CONTRACT,
            ):
                raise ValueError("SCENARIO_ROW_RECEIPT_NOT_PERSISTED")
            stats["supported_receipts_saved"] += 1
        elif row.assessment == "GAP":
            individual_gaps[sid] = list(answer.gaps)
        else:
            blocked[sid] = answer.block_reason
        completed[sid] = row
        origins[sid] = {
            "origin_execution_id": execution_id, "evidence_id": evidence_id,
        }
        outcomes[sid] = outcome
        _LOG.info(
            "scenario_row_checkpointed index=%d/%d assessment=%s scenario_id=%s",
            index, len(pending), row.assessment, sid,
        )
        progress(
            "MODEL_RESULT_SAVED",
            change_id=change_id, execution_id=execution_id,
            scenario_id=sid, assessment=row.assessment,
            observation_saved=True, pass_receipt_saved=row.assessment == "SUPPORTED",
        )

    async def worker() -> None:
        nonlocal started, finished, active, provider_paused, overload_cause
        while not queue.empty():
            if source_unstable or provider_paused:
                return
            try:
                index, sid = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            started += 1
            active += 1
            progress(
                "START", index=index, pending_total=len(pending),
                active=active, queued=len(pending) - started,
                scenario_id=sid,
            )
            try:
                await audit_one(index, sid)
            except Exception as exc:
                kind = _provider_overload_reason(exc)
                if kind is not None and not provider_paused:
                    provider_paused = True
                    overload_cause = kind
                    bridge_runtime = getattr(adapter, "bridge_runtime", None)
                    if bridge_runtime is not None and hasattr(bridge_runtime, "pause_provider"):
                        bridge_runtime.pause_provider()
                    progress(
                        "CIRCUIT_OPEN", cause=kind, finished=finished,
                        queued=queue.qsize(), in_flight=active - 1,
                        recovery="RECONCILE_UNKNOWN_RUNS_THEN_RESUME_MISSING_ROWS",
                    )
                # Keep independent successfully checkpointed rows. Unknown
                # outcomes are never promoted to aggregate PASS.
                errors[sid] = f"{type(exc).__name__}: {exc}"
                _LOG.warning(
                    "scenario_row_failed index=%d/%d scenario_id=%s reason=%s",
                    index, len(pending), sid, errors[sid],
                )
                finished += 1
                progress(
                    "ERROR", index=index, pending_total=len(pending),
                    finished=finished, active=active - 1,
                    scenario_id=sid, execution_id=execution_id,
                    reason=errors[sid],
                )
            else:
                finished += 1
                status = completed[sid].assessment
                progress(
                    "DONE", index=index, pending_total=len(pending),
                    finished=finished, active=active - 1,
                    assessment=status,
                    receipt=status == "SUPPORTED",
                    scenario_id=sid,
                )
            finally:
                active -= 1
                queue.task_done()

    if pending:
        workers = [
            asyncio.create_task(worker())
            for _ in range(min(max_concurrency, len(pending)))
        ]
        # Cancellation propagates: we don't claim completion of in-flight runs.
        await asyncio.gather(*workers)

    if errors or source_unstable or provider_paused:
        progress(
            "PAUSED",
            total=len(plan_ids), reused=len(reused),
            model_dispatches=stats["sdk_calls"],
            attempted=finished, checked=len(completed),
            failed=len(errors), queued=queue.qsize(),
            source_unstable=source_unstable,
            provider_overload=overload_cause or "",
            verified_receipts=stats["supported_receipts_saved"],
            recovery="Reconcile unknown LLM audit runs; restart exact change; reuse validated terminal rows",
        )
        summary = "; ".join(
            f"{sid}={errors[sid]}" for sid in pending if sid in errors
        )
        if source_unstable:
            summary = "SOURCE_UNSTABLE; " + summary
        if overload_cause:
            summary = f"PROVIDER_OVERLOAD_PAUSE={overload_cause}; " + summary
        raise ValueError(
            f"SCENARIO_ROW_QUEUE_INCOMPLETE: {summary}; "
            "RECOVERY: reconcile non-terminal LLM audit runs by original run_id/agent_id; "
            "retry only missing/stale rows after provider recovery, never replay an unknown run"
        )

    ordered = [reused.get(sid) or completed.get(sid) for sid in plan_ids]
    if any(row is None for row in ordered):
        raise ValueError("SCENARIO_ROW_AGGREGATE_INCOMPLETE")
    all_gaps = [g for sid in plan_ids for g in individual_gaps.get(sid, [])]
    block_reasons = [f"{sid}: {blocked[sid]}" for sid in plan_ids if sid in blocked]
    gap_ids = {sid for sid in plan_ids if (reused.get(sid) or completed.get(sid)).assessment == "GAP"}
    blocked_ids = {
        sid for sid in plan_ids
        if (reused.get(sid) or completed.get(sid)).assessment
        in ("CONTRACT_CONFLICT", "INSPECTION_BLOCKED")
    }
    if gap_ids != {g.scenario_id for g in all_gaps} or blocked_ids != set(blocked):
        raise ValueError("SCENARIO_ROW_AGGREGATE_EVIDENCE_INCOMPLETE")
    verdict = (
        "SCENARIO_TEST_COVERAGE_UNKNOWN" if block_reasons
        else "SCENARIO_TEST_COVERAGE_FAIL" if all_gaps
        else "SCENARIO_TEST_COVERAGE_PASS"
    )
    last_outcome = next(
        (outcomes[sid] for sid in reversed(pending) if sid in outcomes), None,
    )
    return ScenarioCoverageVerifyAnswer(
        verdict=verdict,
        plan_evidence_id=plan_id,
        source_digest=current_digest,
        row_reviews=ordered,
        gaps=all_gaps,
        block_reason="; ".join(block_reasons),
        summary=(
            f"Independent row queue: reused {len(reused)}, "
            f"audited {len(completed)}, concurrency <= {max_concurrency}; "
            f"blocked {len(block_reasons)}, gaps {len(all_gaps)}."
        ),
        local_hop_copy_block=(
            f"Workspace: {workspace}\nChange: {change_id}\nVerdict: {verdict}\n"
            f"Reused: {len(reused)}; fresh: {len(completed)}; "
            f"GAP: {len(all_gaps)}; BLOCKED: {len(block_reasons)}"
        ),
    ), last_outcome, origins, stats
