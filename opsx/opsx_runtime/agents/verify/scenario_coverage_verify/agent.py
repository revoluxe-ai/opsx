"""Runtime-агент audit scenario test coverage (read-only, после IMPL_OK)."""

from __future__ import annotations

from opsx.project_context import resource_path

import logging
from pathlib import Path
from typing import Any, Mapping

from agents.common.scenario_files import scenario_filename
from agents.adapters.abc_adapter import (
    agent_session_patch,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.abc_agent import ABCAgent
from agents.check_receipts import (
    SCENARIO_COVERAGE_CONTRACT, best_effort_write_pass, read_pass,
)
from agents.state import GraphState
from agents.verify.scenario_coverage_verify.input_pack import (
    assess_plan_materialization,
    load_scenario_coverage_plan_bundle,
)
from agents.verify.scenario_coverage_verify.prompt.get_prompt import get_prompt, get_delta_prompt
from agents.verify.scenario_coverage_verify.delta_rows import (
    ROW_CONTRACT, _scenario_section, impact_scope, ledger_from_validated_rows,
    row_freshness_token, select_reusable_rows,
)
from agents.verify.scenario_coverage_verify.row_disposition import (
    build_scenario_coverage_row_context,
    resolve_row_work,
)
from agents.verify.scenario_coverage_verify.mapped_test_view import enrich_ledger_mapped_test
from agents.verify.scenario_coverage_verify.evidence_contract import (
    input_snapshot,
    scenario_verifier_contract_digest,
    source_paths,
    review_proof_error,
    spec_clauses,
    trace_mappings,
)
from agents.verify.scenario_coverage_verify.report import format_scenario_coverage_report
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageGap, ScenarioCoverageRowReview, ScenarioCoverageVerifyAnswer,
)
from agents.verify.scenario_coverage_verify.semantic_validation import (
    _SCENARIO_ROW, _audit_completeness_error, _audit_proof_error,
)
from agents.verify.scenario_coverage_verify.row_receipts import (
    read_observation, read_reusable_terminal_row, read_supported_row,
    save_supported_row, write_observation,
)
from agents.verify.scenario_coverage_verify.legacy_reuse import (
    prepare_legacy_reuse, read_legacy_terminal,
)
from agents.verify.scenario_coverage_verify.row_single import (
    DEFAULT_MAX_CONCURRENT_SCENARIO_AGENTS,
    audit_pending_independently,
)
from agents.verify.scenario_coverage_verify.progress import report as progress

_LOGGER = logging.getLogger(__name__)


def _scenario_post_fix_episode_active(state: dict[str, Any], *, execution_id: str) -> bool:
    """True, если в этом же verify-run уже был scenario apply или test-repair hop."""
    run = str(state.get("run_uuid") or execution_id)
    if (
        state.get("verify_scenario_episode_used_repair") is True
        and str(state.get("verify_scenario_repair_run_uuid") or "") == run
    ):
        return True
    if (
        state.get("verify_scenario_episode_used_apply") is True
        and str(state.get("verify_scenario_apply_run_uuid") or "") == run
    ):
        return True
    return False

_READ_TOOLS = ("read",)
_ROW_RECEIPT_CONTRACT = "scenario_row_evidence_v1"


def _blocked_answer(
    *,
    change_id: str,
    workspace: str,
    block_reason: str,
) -> ScenarioCoverageVerifyAnswer:
    return ScenarioCoverageVerifyAnswer(
        verdict="SCENARIO_TEST_COVERAGE_UNKNOWN",
        summary="Audit scenario coverage заблокирован: evidence/input contract не подтверждён.",
        gaps=[],
        block_reason=block_reason,
        local_hop_copy_block=(
            f"Workspace: {workspace}\n"
            f"Change: {change_id}\n"
            f"Verdict: SCENARIO_TEST_COVERAGE_UNKNOWN\n"
            f"Reason: {block_reason}"
        ),
    )


def _answer_to_state_patch(
    *,
    change_id: str,
    answer: ScenarioCoverageVerifyAnswer,
    result: Any | None = None,
) -> dict[str, Any]:
    markers = {
        "SCENARIO_TEST_COVERAGE_PASS": "✅ SCENARIO_COVERAGE_PASS",
        "SCENARIO_TEST_COVERAGE_FAIL": "❌ SCENARIO_COVERAGE_FAIL",
        "SCENARIO_TEST_COVERAGE_UNKNOWN": "⏸ SCENARIO_COVERAGE_BLOCKED",
    }
    marker = markers[answer.verdict]
    report_text = format_scenario_coverage_report(
        change_id=change_id,
        answer=answer,
        marker=marker,
    )
    patch: dict[str, Any] = {
        "scenario_coverage_verdict": answer.verdict,
        "scenario_coverage_marker": marker,
        "scenario_coverage_report_text": report_text,
        "scenario_coverage_answer": answer.model_dump(mode="json"),
        "scenario_coverage_execution_status": "deterministic"
        if result is None
        else getattr(result, "status", "finished"),
        "scenario_coverage_validation_attempts": 0 if result is None else getattr(
            result,
            "validation_attempts",
            1,
        ),
    }
    if result is not None:
        patch.update(
            {
                "scenario_coverage_agent_id": result.agent_id,
                "scenario_coverage_run_id": result.run_id,
                "scenario_coverage_usage": result.usage,
                "scenario_coverage_cost": result.cost,
                "scenario_coverage_cost_status": result.cost_status,
                "scenario_coverage_billing_error": result.billing_error,
                "scenario_coverage_usage_source": result.usage_source,
            },
        )
    else:
        patch.update(
            {
                "scenario_coverage_agent_id": None,
                "scenario_coverage_run_id": None,
                "scenario_coverage_usage": None,
                "scenario_coverage_cost": None,
                "scenario_coverage_cost_status": "not_applicable",
                "scenario_coverage_billing_error": None,
                "scenario_coverage_usage_source": "deterministic",
            },
        )
    return patch


def _validated_row_reuse(
    *,
    repo_root: Path,
    change_id: str,
    state: GraphState,
    plan_ids: list[str],
    mapping: dict[str, str],
    plan_id: str,
    execution_id: str,
    verifier_contract_digest: str,
    source_digest: str,
    reuse_routes: dict[str, str] | None = None,
) -> tuple[
    dict[str, Any] | None, dict[str, ScenarioCoverageRowReview],
    list[str], dict[str, dict], dict[str, dict[str, Any]],
]:
    """Reuse checked terminal results with exact source and authority identity.

    Positive rows require independent PASS receipts. Negative rows require
    validated observations, including original findings, never PASS authority.
    """
    if not execution_id:
        return None, {}, plan_ids, {}, {}
    model = state.get("model")
    if not isinstance(model, Mapping) or not model.get("id"):
        return None, {}, plan_ids, {}, {}
    try:
        scope = impact_scope(
            repo_root, source_paths(repo_root, change_id), mapping,
            authority={
                "model": dict(model), "plan_evidence_id": plan_id,
                "verifier_contract_digest": verifier_contract_digest,
            },
        )
        receipt = read_pass(
            repo_root, change_id=change_id, check="scenario_coverage_rows",
            contract=_ROW_RECEIPT_CONTRACT,
            pass_verdict="ROW_SET_VALID",
            input_digest=scope["shared_digest"],
        )
        candidates, _, provenance = select_reusable_rows(
            plan_ids=plan_ids, mapping=mapping, current_scope=scope,
            prior_ledger=receipt["result"] if receipt is not None else None,
        )
        validated: dict[str, ScenarioCoverageRowReview] = {}
        for sid, raw in candidates.items():
            try:
                row = ScenarioCoverageRowReview.model_validate(raw)
                if row.assessment != "SUPPORTED" or review_proof_error(
                    repo_root, change_id, row, expected_node=mapping[sid],
                ) is not None:
                    continue
            except (ValueError, TypeError, KeyError):
                continue
            validated[sid] = row
            if reuse_routes is not None:
                reuse_routes[sid] = "AGGREGATE_PASS_LEDGER"
        # Per-row receipts and negative observations are independent. A
        # corrupt row misses without invalidating trustworthy sibling rows.
        negative_evidence: dict[str, dict[str, Any]] = {}
        row_ctx = build_scenario_coverage_row_context(
            repo_root,
            change_id,
            authority={
                "model": dict(model),
                "plan_evidence_id": plan_id,
                "verifier_contract_digest": verifier_contract_digest,
            },
        )
        for sid in plan_ids:
            work = resolve_row_work(row_ctx, sid, disposition_mode="batch")
            if work.run_check_repair_cycle or work.row_review is None:
                continue
            row = work.row_review
            origin = dict(work.provenance or {})
            has_positive = work.reason == "FRESH_SUPPORTED_RECEIPT"
            has_negative = work.reason == "DURABLE_GAP_REUSE_BATCH"
            if has_negative and sid in validated:
                raise ValueError(f"SCENARIO_ROW_RECEIPT_CONFLICT: {sid}")
            if has_positive:
                if (
                    sid in validated
                    and row.model_dump(mode="json") != validated[sid].model_dump(mode="json")
                ):
                    validated.pop(sid, None)
                    provenance.pop(sid, None)
                    if reuse_routes is not None:
                        reuse_routes.pop(sid, None)
                    continue
                validated[sid] = row
                provenance[sid] = origin
                if reuse_routes is not None:
                    reuse_routes[sid] = "CURRENT_SUPPORTED_RECEIPT"
            elif has_negative and origin:
                validated[sid] = row
                provenance[sid] = origin
                negative_evidence[sid] = {
                    "gaps": list(work.gaps or ()),
                    "block_reason": work.block_reason or "",
                }
                if reuse_routes is not None:
                    reuse_routes[sid] = "CURRENT_NEGATIVE_OBSERVATION"
        # Deterministic bridge for previously finished rows whose receipt
        # fingerprints included the old broad runtime policy. This never
        # issues a model request and never assigns PASS to negative reviews.
        missing = [sid for sid in plan_ids if sid not in validated]
        if missing:
            prepared = prepare_legacy_reuse(
                repo_root, change_id=change_id, mapping=mapping,
                plan_id=plan_id, model=model,
            )
            if prepared is not None:
                for sid in missing:
                    historical = read_legacy_terminal(
                        repo_root, change_id=change_id, scenario_id=sid,
                        mapping=mapping, plan_id=plan_id, model=model,
                        prepared=prepared,
                    )
                    if historical is None:
                        continue
                    row, gaps, block_reason, origin = historical
                    validated[sid] = row
                    provenance[sid] = origin
                    if row.assessment != "SUPPORTED":
                        negative_evidence[sid] = {
                            "gaps": gaps, "block_reason": block_reason,
                        }
                    if reuse_routes is not None:
                        reuse_routes[sid] = "LEGACY_REATTESTED"
                    _LOGGER.info("scenario_row_legacy_revalidated scenario_id=%s", sid)
        return (
            scope, validated, [sid for sid in plan_ids if sid not in validated],
            provenance, negative_evidence,
        )
    except (ValueError, TypeError, KeyError, OSError) as exc:
        _LOGGER.info("scenario_row_reuse_miss reason=%s", type(exc).__name__)
        return None, {}, plan_ids, {}, {}


def _row_decisions(
    *,
    repo_root: Path,
    change_id: str,
    plan_ids: list[str],
    reused: Mapping[str, ScenarioCoverageRowReview],
    routes: Mapping[str, str],
    origins: Mapping[str, Mapping[str, str]],
    source_digest: str,
    plan_id: str,
    scope: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Report decisions, not inferred model completions or PASS claims.

    Reasons for misses are diagnostics only; they never grant receipt authority.
    """
    decisions: list[dict[str, Any]] = []
    for sid in plan_ids:
        if sid in reused:
            result = {
                "scenario_id": sid,
                "decision": "REUSE",
                "reason": "FRESH_VALIDATED_REVIEW",
                "route": routes.get(sid, "UNKNOWN"),
                "assessment": reused[sid].assessment,
                "origin_execution_id": origins.get(sid, {}).get("origin_execution_id", ""),
            }
        else:
            obs = read_observation(repo_root, change_id=change_id, scenario_id=sid)
            if obs is None:
                reason = "NO_VALID_OBSERVATION_OR_RECEIPT"
            elif obs.get("row_identity_contract") == ROW_CONTRACT:
                if scope["row_digests"].get(sid) is None:
                    reason = "MAPPED_TEST_MISSING"
                elif obs.get("row_digest") != scope["row_digests"].get(sid):
                    reason = "SPEC_OR_TEST_CHANGED"
                else:
                    reason = "RECEIPT_NOT_REUSABLE"
            elif (
                obs.get("row_identity_contract") != ROW_CONTRACT
                and obs.get("plan_evidence_id") != plan_id
            ):
                reason = "LEGACY_PLAN_IDENTITY_CHANGED"
            elif obs.get("shared_digest") and obs["shared_digest"] != scope["shared_digest"]:
                reason = "REVIEW_AUTHORITY_OR_SHARED_INPUT_CHANGED"
            elif (
                obs.get("shared_digest") == scope["shared_digest"]
                and obs.get("row_digest") != scope["row_digests"].get(sid)
            ):
                reason = "ROW_IDENTITY_CHANGED"
            elif obs.get("source_digest") != source_digest:
                # Legacy contracts tied row freshness to the full audit closure.
                reason = "SOURCE_DIGEST_OR_CONTRACT_VERSION_CHANGED"
            elif obs.get("row_digest") != scope["row_digests"].get(sid):
                reason = "ROW_IDENTITY_CHANGED"
            else:
                reason = "RECEIPT_NOT_REUSABLE"
            result = {
                "scenario_id": sid,
                "decision": "MODEL_QUEUED",
                "reason": reason,
                "route": "NONE",
                "assessment": "",
                "origin_execution_id": "",
            }
        decisions.append(result)
    return decisions


def _report_decisions(
    decisions: list[dict[str, Any]], *, execution_id: str, change_id: str,
) -> None:
    for item in decisions:
        progress(
            "ROW_DECISION",
            change_id=change_id, execution_id=execution_id,
            scenario_id=item["scenario_id"], decision=item["decision"],
            reason=item["reason"], route=item["route"],
            skip_model=item["decision"] == "REUSE",
            assessment=item["assessment"],
            origin_execution_id=item["origin_execution_id"],
        )



class ScenarioCoverageVerifyAgent(ABCAgent):
    """Read-only audit: тесты vs durable ``scenario-coverage/plan.md`` (+ receipt)."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Выполнить scenario-coverage audit hop и вернуть structured verdict."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)
        repo_root = Path(repo)

        plan_bundle = load_scenario_coverage_plan_bundle(change_id, repo_root=repo_root)
        block_reason = assess_plan_materialization(plan_bundle)
        if block_reason is not None:
            progress(
                "PREFLIGHT_PAUSE", change_id=change_id,
                execution_id=str(state.get("run_uuid") or ""),
                reason="PLAN_NOT_MATERIALIZED", detail=block_reason,
            )
            answer = _blocked_answer(
                change_id=change_id,
                workspace=workspace,
                block_reason=block_reason,
            )
            return _answer_to_state_patch(change_id=change_id, answer=answer)

        # Snapshot covers the change's coverage plan, specs and mapped tests.
        # Per-row reuse uses only the individual scenario, mapping and test
        # file; unrelated production changes do not invalidate row evidence.
        try:
            snapshot = input_snapshot(repo_root, change_id)
            verifier_contract_digest = scenario_verifier_contract_digest(
                repo_root, state.get("model"),
            )
            mapping = trace_mappings(repo_root, change_id)
            declared = plan_bundle.get("scenario_ids")
            if isinstance(declared, list) and declared:
                plan_ids = [str(item) for item in declared]
            else:
                plan_ids = _SCENARIO_ROW.findall(str(plan_bundle["plan_text"]))
            focus = str(state.get("scenario_row_focus") or "").strip()
            if focus:
                if focus not in plan_ids:
                    raise ValueError(f"AUDIT_FOCUS_UNKNOWN: {focus}")
                plan_bundle = {**plan_bundle, "scenario_ids": [focus]}
                if focus not in mapping or not str(mapping.get(focus) or "").strip():
                    gap = ScenarioCoverageGap(
                        scenario_id=focus,
                        gap_class="MISSING_OR_WEAK_SCENARIO_TEST",
                        evidence="Нет mapping или test-файла для этой строки",
                    )
                    try:
                        focus_digest = str(input_snapshot(repo_root, change_id)["source_digest"])
                    except (ValueError, OSError):
                        focus_digest = "source-snapshot-unavailable"
                    answer = ScenarioCoverageVerifyAnswer(
                        verdict="SCENARIO_TEST_COVERAGE_FAIL",
                        plan_evidence_id=str(
                            (plan_bundle.get("plan_receipt") or {}).get("PLAN_EVIDENCE_ID") or ""
                        ),
                        source_digest=focus_digest,
                        row_reviews=[ScenarioCoverageRowReview(
                            scenario_id=focus, test_node_id="", assessment="GAP",
                            evidence=gap.evidence,
                        )],
                        gaps=[gap],
                        summary="Строка без mapping уходит в repair",
                        local_hop_copy_block=f"Change: {change_id}\nScenario: {focus}\nGAP",
                    )
                    patch = _answer_to_state_patch(change_id=change_id, answer=answer)
                    try:
                        patch["scenario_coverage_source_snapshot"] = input_snapshot(repo_root, change_id)
                    except (ValueError, OSError):
                        patch["scenario_coverage_source_snapshot"] = {"source_digest": ""}
                    return patch
                plan_ids = [focus]
                mapping = {focus: mapping[focus]}
            elif (
                not plan_ids
                or len(plan_ids) != len(set(plan_ids))
                or set(plan_ids) != set(mapping)
            ):
                raise ValueError("AUDIT_MAPPING_INCOMPLETE: matrix and frozen plan differ")
            ledger = []
            change_dir = resource_path(repo_root, "openspec", "changes", change_id)
            for scenario_id in plan_ids:
                when, outcomes = spec_clauses(repo_root, change_id, scenario_id)
                try:
                    section = _scenario_section(
                        repo_root, change_dir, scenario_id, set(),
                    ).decode("utf-8", errors="replace")
                except ValueError:
                    section = ""
                ledger.append(enrich_ledger_mapped_test(
                    repo_root,
                    {
                        "scenario_id": scenario_id,
                        "test_node_id": mapping[scenario_id],
                        "scenario_file": (
                            f"openspec/changes/{change_id}/scenario-coverage/scenarios/"
                            f"{scenario_filename(scenario_id)}"
                        ),
                        "when_clauses": when,
                        "outcome_clauses": outcomes,
                        "spec_section": section,
                    },
                ))
            focus_token = (
                row_freshness_token(repo_root, change_id, focus, mapping[focus])
                if focus else ""
            )
        except (ValueError, OSError, KeyError) as exc:
            progress(
                "PREFLIGHT_PAUSE", change_id=change_id,
                execution_id=str(state.get("run_uuid") or ""),
                reason="AUDIT_INPUT_INVALID", detail=f"{type(exc).__name__}: {exc}",
            )
            return _answer_to_state_patch(
                change_id=change_id,
                answer=_blocked_answer(
                    change_id=change_id, workspace=workspace,
                    block_reason=f"AUDIT_INPUT_INVALID: {type(exc).__name__}: {exc}",
                ),
            )

        # No SDK call if the same audited source has a durable, verified PASS.
        # Check the frozen plan completeness cheaply, but do not repeat all
        # semantic proof inspection unless the source fingerprint changed.
        execution_id = str(state.get("run_uuid") or "").strip()
        plan_id = str((plan_bundle.get("plan_receipt") or {}).get("PLAN_EVIDENCE_ID") or "")
        if execution_id and not focus:
            cached = read_pass(
                repo_root,
                change_id=change_id,
                check="scenario_coverage",
                contract=SCENARIO_COVERAGE_CONTRACT,
                pass_verdict="SCENARIO_TEST_COVERAGE_PASS",
                input_digest=snapshot["source_digest"],
                verifier_contract_digest=verifier_contract_digest,
            )
            if cached is not None:
                try:
                    answer = ScenarioCoverageVerifyAnswer.model_validate(cached["result"])
                except (ValueError, TypeError):
                    answer = None
                if (
                    answer is not None
                    and answer.verdict == "SCENARIO_TEST_COVERAGE_PASS"
                    and answer.source_digest == snapshot["source_digest"]
                    and answer.plan_evidence_id == plan_id
                    and _audit_completeness_error(answer, plan_bundle) is None
                ):
                    _LOGGER.info(
                        "scenario_coverage reusable_pass source_digest=%s execution_id=%s "
                        "origin_execution_id=%s sdk_calls=0",
                        snapshot["source_digest"], execution_id,
                        cached["origin_execution_id"],
                    )
                    decisions = [
                        {
                            "scenario_id": sid, "decision": "REUSE",
                            "reason": "FRESH_AGGREGATE_PASS",
                            "route": "AGGREGATE_PASS_RECEIPT",
                            "assessment": "SUPPORTED",
                            "origin_execution_id": cached["origin_execution_id"],
                        }
                        for sid in plan_ids
                    ]
                    _report_decisions(decisions, execution_id=execution_id, change_id=change_id)
                    progress(
                        "SUMMARY", total=len(plan_ids), reused=len(plan_ids),
                        legacy_reused=0, model_queued=0, model_dispatches=0,
                        model_results_saved=0, fresh=0, sdk_calls=0,
                        supported=len(plan_ids), gaps=0, blocked=0,
                        verdict=answer.verdict,
                    )
                    patch = _answer_to_state_patch(
                        change_id=change_id, answer=answer,
                    )
                    patch.update({
                        "scenario_coverage_source_snapshot": snapshot,
                        "scenario_coverage_selection": decisions,
                        "scenario_coverage_model_dispatches": 0,
                        "scenario_coverage_sdk_calls": 0,
                        "scenario_coverage_execution_status": "REUSED_PASS",
                        "scenario_coverage_convergence": {
                            "status": "REUSED_PASS", "previous": [], "current": [],
                        },
                        "scenario_coverage_execution_id": execution_id,
                        "scenario_coverage_receipt": {
                            "source": "durable_pass",
                            "origin_execution_id": cached["origin_execution_id"],
                            "evidence_id": cached["evidence_id"],
                            "current_execution_id": execution_id,
                            "input_digest": snapshot["source_digest"],
                    "verifier_contract_digest": verifier_contract_digest,
                        },
                    })
                    return patch

        reuse_routes: dict[str, str] = {}
        scope, reused, pending, row_origins, reused_evidence = _validated_row_reuse(
            repo_root=repo_root, change_id=change_id, state=state,
            plan_ids=plan_ids, mapping=mapping, plan_id=plan_id,
            execution_id=execution_id,
            verifier_contract_digest=verifier_contract_digest,
            source_digest=snapshot["source_digest"],
            reuse_routes=reuse_routes,
        )
        if scope is None:
            progress(
                "PREFLIGHT_PAUSE", change_id=change_id, execution_id=execution_id,
                reason="SCENARIO_ROW_AUTHORITY_SCOPE_UNAVAILABLE",
            )
            return _answer_to_state_patch(
                change_id=change_id,
                answer=_blocked_answer(
                    change_id=change_id, workspace=workspace,
                    block_reason="SCENARIO_ROW_AUTHORITY_SCOPE_UNAVAILABLE",
                ),
            )
        decisions = _row_decisions(
            repo_root=repo_root, change_id=change_id, plan_ids=plan_ids,
            reused=reused, routes=reuse_routes, origins=row_origins,
            source_digest=snapshot["source_digest"], plan_id=plan_id, scope=scope,
        )
        _report_decisions(decisions, execution_id=execution_id, change_id=change_id)
        legacy_reused = sum(item["route"] == "LEGACY_REATTESTED" for item in decisions)
        progress(
            "SELECTION", change_id=change_id, total=len(plan_ids), reused=len(reused),
            legacy_reused=legacy_reused, model_queued=len(pending),
            source_digest=snapshot["source_digest"],
            reviewer_digest=verifier_contract_digest,
            max_parallel=DEFAULT_MAX_CONCURRENT_SCENARIO_AGENTS, execution_id=execution_id,
        )
        if reused:
            _LOGGER.info(
                "scenario_coverage_delta reused=%d fresh=%d execution_id=%s",
                len(reused), len(pending), execution_id,
            )

        if reused and not pending:
            # Assemble cached terminal results through the strict live row
            # aggregator. An empty pending queue cannot invoke an LLM audit.
            try:
                answer, _, _, _ = await audit_pending_independently(
                    adapter=self._adapter, state=state,
                    repo_root=repo_root, workspace=workspace, change_id=change_id,
                    plan_ids=plan_ids, mapping=mapping, plan_id=plan_id,
                    snapshot=snapshot, evidence_ledger=ledger,
                    pending=[], reused=reused,
                    reused_evidence=reused_evidence,
                    row_digests=scope["row_digests"],
                    shared_digest=scope["shared_digest"],
                    execution_id=execution_id,
                    validation_attempts=self.validation_max_attempts,
                )
                error = _audit_completeness_error(answer, plan_bundle)
                if error is None:
                    error = _audit_proof_error(
                        answer, repo_root=repo_root, change_id=change_id,
                        source_snapshot=snapshot, mappings=mapping,
                    )
                if focus:
                    if row_freshness_token(repo_root, change_id, focus, mapping[focus]) != focus_token:
                        error = "ROW_INPUTS_CHANGED: spec section or test file changed during the check"
                elif input_snapshot(repo_root, change_id)["source_digest"] != snapshot["source_digest"]:
                    error = "AUDIT_STALE: source changed during row reuse"
                previous = state.get("scenario_coverage_answer")
                if (
                    error is None
                    and answer.verdict != "SCENARIO_TEST_COVERAGE_PASS"
                    and _scenario_post_fix_episode_active(state, execution_id=execution_id)
                    and isinstance(previous, dict)
                    and previous.get("source_digest") == snapshot["source_digest"]
                ):
                    error = (
                        "AUDIT_NON_CONVERGENT: scenario repair/apply returned "
                        "without changing audited source"
                    )
            except (ValueError, TypeError, OSError) as exc:
                error = f"SCENARIO_ROW_REUSE_INVALID: {type(exc).__name__}: {exc}"
            if error is not None:
                progress(
                    "AGGREGATE_PAUSE", change_id=change_id, execution_id=execution_id,
                    reason=error, reused=len(reused), model_dispatches=0,
                )
                return _answer_to_state_patch(
                    change_id=change_id,
                    answer=_blocked_answer(
                        change_id=change_id, workspace=workspace, block_reason=error,
                    ),
                )
            progress(
                "SUMMARY", total=len(plan_ids), reused=len(reused),
                legacy_reused=legacy_reused, model_queued=0, model_dispatches=0,
                model_results_saved=0, fresh=0, sdk_calls=0,
                supported=sum(r.assessment == "SUPPORTED" for r in answer.row_reviews),
                gaps=sum(r.assessment == "GAP" for r in answer.row_reviews),
                blocked=sum(
                    r.assessment in ("CONTRACT_CONFLICT", "INSPECTION_BLOCKED")
                    for r in answer.row_reviews
                ),
                verdict=answer.verdict,
            )
            patch = _answer_to_state_patch(change_id=change_id, answer=answer)
            patch.update({
                "scenario_coverage_source_snapshot": snapshot,
                "scenario_coverage_execution_status": "REUSED_ROWS",
                "scenario_coverage_selection": decisions,
                "scenario_coverage_execution_id": execution_id,
                "scenario_coverage_model_dispatches": 0,
                "scenario_coverage_sdk_calls": 0,
                "scenario_coverage_convergence": {
                    "status": "REUSED_ROWS", "previous": [], "current": [],
                },
                "scenario_coverage_receipt": {
                    "source": "durable_rows", "reused_rows": len(reused),
                    "fresh_rows": 0, "current_execution_id": execution_id,
                    "input_digest": snapshot["source_digest"],
                    "verifier_contract_digest": verifier_contract_digest,
                },
            })
            return patch

        _LOGGER.info(
            "scenario_coverage_model_queue pending=%d reused=%d execution=%s",
            len(pending), len(reused), execution_id,
        )
        try:
            answer, outcome, fresh_origins, checkpoint_stats = (
                await audit_pending_independently(
                    adapter=self._adapter,
                    state=state,
                    repo_root=repo_root,
                    workspace=workspace,
                    change_id=change_id,
                    plan_ids=plan_ids,
                    mapping=mapping,
                    plan_id=plan_id,
                    snapshot=snapshot,
                    evidence_ledger=ledger,
                    pending=pending,
                    reused=reused,
                    reused_evidence=reused_evidence,
                    row_digests=scope["row_digests"],
                    shared_digest=scope["shared_digest"],
                    execution_id=execution_id,
                    validation_attempts=self.validation_max_attempts,
                )
            )
        except (ValueError, TypeError, OSError) as exc:
            progress(
                "AUDIT_PAUSE", change_id=change_id, execution_id=execution_id,
                reason="SCENARIO_ROW_AUDIT_PAUSED",
                detail=f"{type(exc).__name__}: {exc}",
            )
            return _answer_to_state_patch(
                change_id=change_id,
                answer=_blocked_answer(
                    change_id=change_id, workspace=workspace,
                    block_reason=f"SCENARIO_ROW_AUDIT_PAUSED: {type(exc).__name__}: {exc}",
                ),
            )
        audit_error = _audit_completeness_error(answer, plan_bundle)
        if audit_error is None:
            audit_error = _audit_proof_error(
                answer, repo_root=repo_root, change_id=change_id,
                source_snapshot=snapshot, mappings=mapping,
            )
        try:
            snapshot_after = input_snapshot(repo_root, change_id)
        except (ValueError, OSError) as exc:
            snapshot_after = None
            audit_error = f"AUDIT_SOURCE_UNAVAILABLE: {type(exc).__name__}: {exc}"
        if focus:
            if row_freshness_token(repo_root, change_id, focus, mapping[focus]) != focus_token:
                audit_error = "ROW_INPUTS_CHANGED: spec section or test file changed during the check"
        elif snapshot_after is not None and (
            snapshot_after["source_digest"] != snapshot["source_digest"]
        ):
            audit_error = "AUDIT_STALE: source changed during the audit"

        # Same-execution re-audit must make causal progress rather than simply
        # repeat an unchanged finding set with a different verbal explanation.
        previous = state.get("scenario_coverage_answer")
        convergence: dict[str, Any] = {"status": "INITIAL", "previous": [], "current": []}
        if (
            _scenario_post_fix_episode_active(state, execution_id=str(state.get("run_uuid") or ""))
            and isinstance(previous, dict)
            and previous.get("source_digest")
        ):
            old_ids = {
                str(g.get("scenario_id")) for g in previous.get("gaps") or []
                if isinstance(g, dict) and g.get("scenario_id")
            }
            new_ids = {
                str(g.scenario_id) for g in answer.gaps
            }
            old_digest = str(previous.get("source_digest"))
            convergence = {
                "status": (
                    "NO_SOURCE_CHANGE" if old_digest == snapshot["source_digest"]
                    else "REGRESSION" if new_ids - old_ids
                    else "NO_PROGRESS" if not (new_ids < old_ids)
                    else "REDUCED"
                ),
                "previous": sorted(old_ids),
                "current": sorted(new_ids),
                "previous_source_digest": old_digest,
                "current_source_digest": snapshot["source_digest"],
            }
            if audit_error is None and answer.verdict == "SCENARIO_TEST_COVERAGE_FAIL":
                if convergence["status"] == "NO_SOURCE_CHANGE":
                    audit_error = (
                        "AUDIT_NON_CONVERGENT: scenario repair/apply returned without "
                        "changing audited source; original gaps remain"
                    )
                elif convergence["status"] in ("NO_PROGRESS", "REGRESSION"):
                    audit_error = (
                        f"AUDIT_NON_CONVERGENT: {convergence['status']}; "
                        f"original={sorted(old_ids)}; current={sorted(new_ids)}"
                    )

        if audit_error is not None:
            progress(
                "AGGREGATE_PAUSE", change_id=change_id, execution_id=execution_id,
                reason=audit_error, reused=len(reused),
                model_dispatches=checkpoint_stats["sdk_calls"],
            )
            answer = _blocked_answer(
                change_id=change_id, workspace=workspace, block_reason=audit_error,
            )
        # Every row was already atomically checkpointed after its own LLM-audit hop.
        # Never write a whole-plan PASS receipt using the last row's audit run id.
        saved = False
        row_saved = checkpoint_stats["supported_receipts_saved"] > 0
        row_receipts_saved = checkpoint_stats["supported_receipts_saved"]
        observations_saved = checkpoint_stats["observations_saved"]
        # An aggregate is not a single LLM audit: never attribute all rows to
        # the last completed agent, whose cost/session is only one of many.
        progress(
            "SUMMARY",
            total=len(plan_ids), reused=len(reused),
            legacy_reused=legacy_reused, model_queued=len(pending),
            model_dispatches=checkpoint_stats["sdk_calls"],
            model_results_saved=checkpoint_stats["observations_saved"],
            fresh=checkpoint_stats["observations_saved"],
            sdk_calls=checkpoint_stats["sdk_calls"],
            supported=sum(row.assessment == "SUPPORTED" for row in answer.row_reviews),
            gaps=sum(row.assessment == "GAP" for row in answer.row_reviews),
            blocked=sum(
                row.assessment in ("CONTRACT_CONFLICT", "INSPECTION_BLOCKED")
                for row in answer.row_reviews
            ),
            verdict=answer.verdict,
        )
        patch = _answer_to_state_patch(change_id=change_id, answer=answer)
        patch["scenario_coverage_execution_status"] = "ROW_QUEUE_AGGREGATED"
        patch["scenario_coverage_selection"] = decisions
        patch["scenario_coverage_model_dispatches"] = checkpoint_stats["sdk_calls"]
        patch["scenario_coverage_sdk_calls"] = checkpoint_stats["sdk_calls"]
        patch["scenario_coverage_max_concurrency"] = DEFAULT_MAX_CONCURRENT_SCENARIO_AGENTS
        patch["scenario_coverage_receipt"] = {
            "source": "delta_audit" if reused else "fresh_audit",
            "saved": saved, "row_saved": row_saved,
            "individual_receipts_saved": row_receipts_saved,
            "row_observations_saved": observations_saved,
            "reused_rows": len(reused), "fresh_rows": len(pending),
            "current_execution_id": execution_id,
            "input_digest": snapshot["source_digest"],
                    "verifier_contract_digest": verifier_contract_digest,
        }
        patch["scenario_coverage_source_snapshot"] = snapshot
        patch["scenario_coverage_convergence"] = convergence
        patch["scenario_coverage_execution_id"] = str(state.get("run_uuid") or "")
        return patch
