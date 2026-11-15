"""Последовательная обработка Repair Units без повторного GroupError/RepairOrder.

После первого diagnostic hop очередь ``remaining_queue`` обслуживается внутри одного
outer batch: rotate ``repair_order.ordered_units``, anchor-only diagnostic, dispatch.
Полный pytest/pyright/mypy — один раз в начале batch (если не было PASS) и обязательно
в конце batch перед ``VERIFY_OK``.
"""

from __future__ import annotations

from typing import Any, Literal

from agents.state import GraphState
from agents.verify.dispatch_mode import resolve_verify_dispatch_mode
from agents.verify.dispatcher.flatten_order import pending_finding_ids
from agents.verify.diagnostic.enrichment import build_remaining_queue
from agents.verify.diagnostic.per_finding import build_per_finding_diagnostic
from agents.verify.diagnostic.schemas import (
    DiagnosticResult,
    RemainingQueueUnit,
    RepairUnit,
    source_identity_from_repair_order,
)
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import OrderedUnit, RepairOrderResult
from agents.verify.dispatch_unit.repro_cohort import failure_fingerprints
from workflows.verify.outer_loop import reset_dispatcher_pass_for_next_unit

OuterRouteDecision = Literal[
    "terminal_ok",
    "terminal_cap",
    "terminal_pause",
    "next_unit",
    "final_gates",
    "batch_recheck_gates",
    "full_cycle",
    "harness_recovery",
]

HARNESS_RECOVERY_NO_HARNESS_UNIT = "HARNESS_RECOVERY_NO_HARNESS_UNIT"

# Пути, после правки которых старая repair-очередь недостоверна без полного gate-run.
_QUEUE_INVALIDATION_PATH_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "pyrightconfig.json",
    "mypy.ini",
    "setup.cfg",
    "conftest.py",
    "pytest.ini",
    "/fixtures/",
    "/typing/",
    "/protocol",
    "configs/",
)


def _cohort_pause_no_progress(state: GraphState, *, detail: str) -> dict[str, Any]:
    """Terminal PAUSE без повторного FULL_SUITE pytest после repair без прогресса."""
    from workflows.verify.routing_log import log_gate_repair_stop

    iteration = int(state.get("verify_outer_iteration") or 0)
    patch = {
        **reset_outer_batch_patch(),
        "verify_outer_iteration": iteration,
        "verify_loop_status": "PAUSE",
        "verify_loop_terminal": True,
        "verify_post_reset_route": None,
        "owner_attention": detail,
        "stop_kind": "COHORT_NO_CONVERGENCE",
    }
    log_gate_repair_stop(
        {**state, **patch},
        stop_kind="COHORT_NO_CONVERGENCE",
        source="verify_reset_repair_batch",
    )
    return patch


def _cohort_restart_or_pause(state: GraphState, *, detail: str) -> dict[str, Any]:
    """До N полных перезапусков после COHORT failure, иначе terminal PAUSE."""
    from workflows.verify.outer_loop import resolve_cohort_max_restarts

    count = int(state.get("verify_cohort_restart_count") or 0)
    max_restarts = resolve_cohort_max_restarts(state)
    if count < max_restarts:
        next_count = count + 1
        return {
            **reset_outer_batch_patch(),
            "verify_cohort_restart_count": next_count,
            "verify_loop_status": "IN_PROGRESS",
            "verify_loop_terminal": False,
            "verify_post_reset_route": "full_restart",
            "owner_attention": (
                f"COHORT_RESTART: attempt {next_count}/{max_restarts}; {detail}"
            ),
        }
    iteration = int(state.get("verify_outer_iteration") or 0) + 1
    return {
        **reset_outer_batch_patch(),
        "verify_outer_iteration": iteration,
        "verify_loop_status": "PAUSE",
        "verify_loop_terminal": True,
        "owner_attention": (
            f"cohort_restart_exhausted after {max_restarts} attempts; {detail}"
        ),
    }


def reset_outer_batch_patch() -> dict[str, Any]:
    """Сбросить поля outer batch (перед полным outer cycle или после финальных gates)."""
    return {
        "verify_outer_batch_active": False,
        "verify_outer_batch_final_pending": False,
        "verify_outer_route_decision": None,
        "verify_outer_batch_stall_key": None,
        "verify_outer_batch_stall_count": 0,
    }


def _resolved_finding_ids(state: GraphState) -> set[str]:
    raw = state.get("dispatcher_resolved_finding_ids")
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw if x}


def _load_repair_order(state: GraphState) -> RepairOrderResult | None:
    raw = state.get("repair_order_result")
    if not isinstance(raw, dict):
        return None
    return RepairOrderResult.model_validate(raw)


def _load_diagnostic(state: GraphState) -> DiagnosticResult | None:
    raw = state.get("diagnostic_result")
    if not isinstance(raw, dict):
        return None
    return DiagnosticResult.model_validate(raw)


def _unit_map(repair_order: RepairOrderResult) -> dict[str, OrderedUnit]:
    return {u.unit_id: u for u in repair_order.ordered_units}


def _finding_paths(analysis: GroupErrorResult | None) -> dict[str, str]:
    if analysis is None:
        return {}
    out: dict[str, str] = {}
    for f in analysis.findings:
        obs = f.observation or ""
        path = obs.split(":", 1)[0].strip()
        if path:
            out[f.finding_id] = path
    return out


def collect_batch_files_changed(state: GraphState) -> set[str]:
    """Собрать пути файлов, затронутых repair hop-ами текущего batch pass."""
    files: set[str] = set()
    for row in state.get("dispatcher_outcomes") or []:
        if not isinstance(row, dict):
            continue
        outcome = row.get("outcome")
        if not isinstance(outcome, dict):
            continue
        repair = outcome.get("repair")
        if isinstance(repair, dict):
            fix = repair.get("fix") if isinstance(repair.get("fix"), dict) else repair
            if isinstance(fix, dict):
                changed = fix.get("files_changed")
                if isinstance(changed, list):
                    files.update(str(p) for p in changed if p)
        verify_results = outcome.get("verify_results")
        if isinstance(verify_results, dict):
            for path in verify_results.get("files_changed") or []:
                if isinstance(path, str) and path:
                    files.add(path)
    fix = state.get("verify_finding_fix")
    if isinstance(fix, dict):
        changed = fix.get("files_changed")
        if isinstance(changed, list):
            files.update(str(p) for p in changed if p)
    # Do not include a global commit outcome from a PREVIOUS outer cycle.
    # Current unit changes are attached to its dispatcher outcome instead.
    return files


def queue_invalidation_reason(
    state: GraphState,
    *,
    files_changed: set[str],
    next_unit: RemainingQueueUnit | None,
) -> str | None:
    """Вернуть причину invalidation или ``None`` если очередь ещё безопасна."""
    for path in files_changed:
        norm = path.replace("\\", "/").lower()
        for marker in _QUEUE_INVALIDATION_PATH_MARKERS:
            if marker in norm:
                return f"shared_or_infra_path_changed:{path}"
    if next_unit is None:
        return None
    repair_order = _load_repair_order(state)
    analysis_raw = state.get("analysis_result")
    analysis = (
        GroupErrorResult.model_validate(analysis_raw)
        if isinstance(analysis_raw, dict)
        else None
    )
    if repair_order is None or analysis is None:
        return "missing_repair_order_or_analysis"
    unit = _unit_map(repair_order).get(next_unit.unit_id)
    if unit is None:
        return f"unknown_remaining_unit:{next_unit.unit_id}"
    paths = _finding_paths(analysis)
    unit_paths = {paths.get(fid, "") for fid in unit.finding_ids}
    unit_paths.discard("")
    for path in files_changed:
        norm = path.replace("\\", "/")
        for up in unit_paths:
            if up and norm != up and not norm.endswith(up) and up not in norm:
                if any(
                    marker in norm
                    for marker in ("typing", "protocol", "interface", "schema")
                ):
                    return f"cross_cutting_change:{path}"
    return None


def filter_remaining_queue(
    remaining: list[RemainingQueueUnit],
    repair_order: RepairOrderResult,
    resolved: set[str],
    *,
    per_finding: bool = False,
) -> tuple[list[RemainingQueueUnit], str | None]:
    """Убрать полностью resolved units; при частичном coverage — pause reason."""
    if per_finding:
        pending = pending_finding_ids(repair_order, resolved=resolved)
        if not pending:
            return [], None
        unit_map = _unit_map(repair_order)
        seen: set[str] = set()
        out: list[RemainingQueueUnit] = []
        for fid in pending[1:]:
            for unit in repair_order.ordered_units:
                if fid not in unit.finding_ids or unit.unit_id in seen:
                    continue
                seen.add(unit.unit_id)
                out.append(
                    RemainingQueueUnit(
                        position=unit.position,
                        unit_type=unit.unit_type,
                        unit_id=unit.unit_id,
                    ),
                )
                break
        return out, None
    unit_map = _unit_map(repair_order)
    out: list[RemainingQueueUnit] = []
    for rq in remaining:
        unit = unit_map.get(rq.unit_id)
        if unit is None:
            continue
        fids = list(unit.finding_ids)
        if not fids:
            out.append(rq)
            continue
        resolved_count = sum(1 for fid in fids if fid in resolved)
        if resolved_count == len(fids):
            continue
        if 0 < resolved_count < len(fids):
            return [], (
                f"partial_unit_resolution:{rq.unit_id} "
                f"({resolved_count}/{len(fids)} findings resolved)"
            )
        out.append(rq)
    return out, None


def shrink_repair_order(
    repair_order: RepairOrderResult,
    remaining: list[RemainingQueueUnit],
) -> RepairOrderResult:
    """Оставить в ``ordered_units`` только units из ``remaining`` (порядок repair_order)."""
    keep = {rq.unit_id for rq in remaining}
    units = [u for u in repair_order.ordered_units if u.unit_id in keep]
    return repair_order.model_copy(update={"ordered_units": units})


def build_harness_recovery_dispatch_patch(
    repair_order: RepairOrderResult,
) -> dict[str, Any]:
    """Inject head harness unit в dispatcher (outer batch recovery).

    Single-hop: в patch остаётся только первый harness unit в ``ordered_units``;
    прочие units из прежнего repair_order не возвращаются. При нескольких harness
    units в очереди recovery берёт только ``harness_units[0]``.
    """
    from agents.verify.pytest_run_health import is_harness_blocker

    harness_units = [
        u
        for u in repair_order.ordered_units
        if any(is_harness_blocker(r.repair_workflow) for r in u.finding_workflows)
    ]
    if not harness_units:
        return {}
    head = harness_units[0]
    remaining = [
        RemainingQueueUnit(unit_id=head.unit_id, finding_ids=list(head.finding_ids)),
    ]
    shrunk = shrink_repair_order(
        repair_order.model_copy(update={"ordered_units": [head]}),
        remaining,
    )
    next_diagnostic = build_anchor_only_diagnostic(shrunk)
    return {
        "verify_outer_batch_active": True,
        "repair_order_result": shrunk.model_dump(mode="json"),
        "diagnostic_result": next_diagnostic.model_dump(mode="json"),
    }


def build_anchor_only_diagnostic(
    repair_order: RepairOrderResult,
    *,
    analysis: GroupErrorResult | None = None,
    resolved: set[str] | None = None,
    per_finding: bool = False,
) -> DiagnosticResult:
    """Детерминированный diagnostic для head unit без LLM (следующий hop batch)."""
    if per_finding and analysis is not None:
        return build_per_finding_diagnostic(
            repair_order,
            analysis,
            resolved=resolved or set(),
        )
    if not repair_order.ordered_units:
        raise ValueError("repair_order.ordered_units empty")
    head = repair_order.ordered_units[0]
    repair_unit = RepairUnit(
        unit_ids=[head.unit_id],
        finding_ids=list(head.finding_ids),
        affected_areas=["application_code"],
        root_cause_summary="batch anchor-only diagnostic (no LLM)",
        change_boundaries="minimal unit scope",
    )
    remaining = build_remaining_queue(repair_order, {head.position})
    return DiagnosticResult(
        status="DIAGNOSED",
        source_identity=source_identity_from_repair_order(repair_order),
        anchor_unit_id=head.unit_id,
        repair_unit=repair_unit,
        remaining_queue=remaining,
        pause_reason=None,
    )


def _unknown_status_for_unit(
    state: GraphState,
    unit: OrderedUnit,
) -> bool:
    """True если для finding unit есть UNKNOWN после последнего verify."""
    for row in state.get("dispatcher_outcomes") or []:
        if not isinstance(row, dict):
            continue
        outcome = row.get("outcome")
        if not isinstance(outcome, dict):
            continue
        verify_results = outcome.get("verify_results")
        if not isinstance(verify_results, dict):
            continue
        for st in verify_results.get("member_statuses") or []:
            if not isinstance(st, dict):
                continue
            fid = str(st.get("finding_id") or "")
            if fid in unit.finding_ids and st.get("status") == "UNKNOWN":
                return True
    return False



def _latest_cohort_unknown(state: GraphState) -> str | None:
    """Any unverified current cohort must stop; not be silently dropped."""
    if resolve_verify_dispatch_mode(state) == "per_finding":
        return None
    for row in reversed(state.get("dispatcher_outcomes") or []):
        if not isinstance(row, dict):
            continue
        outcome = row.get("outcome")
        if not isinstance(outcome, dict):
            continue
        verified = outcome.get("verify_results")
        if not isinstance(verified, dict):
            continue
        unknown = [
            f"{s.get('finding_id')}: {s.get('detail')}"
            for s in verified.get("member_statuses") or []
            if isinstance(s, dict) and s.get("status") in ("UNKNOWN", "STALE")
        ]
        if unknown:
            return "COHORT_VERIFICATION_INCONCLUSIVE: " + "; ".join(unknown[:5])
        break
    return None


def decide_outer_route_after_unit(
    state: GraphState,
) -> tuple[OuterRouteDecision, dict[str, Any]]:
    """Решение маршрута после завершения dispatch_cycle для одной Repair Unit."""
    from workflows.verify.outer_loop import (
        is_verify_clean,
        resolve_outer_max_iterations,
    )

    if state.get("verify_batch_recheck_required"):
        next_count = int(state.get("batch_recheck_count") or 0) + 1
        max_iter = resolve_outer_max_iterations(state)
        if next_count > max_iter:
            from workflows.verify.routing_log import log_gate_repair_stop

            patch: dict[str, Any] = {
                "verify_loop_status": "VERIFY_LOOP_CAP",
                "verify_loop_terminal": True,
                "verify_batch_recheck_required": False,
                "batch_recheck_count": next_count,
            }
            log_gate_repair_stop(
                {**state, **patch},
                stop_kind="VERIFY_LOOP_CAP",
                source="decide_outer_route_after_unit.batch_recheck_cap",
            )
            return "terminal_cap", patch
        return "batch_recheck_gates", {
            "verify_batch_recheck_required": False,
            "verify_force_fresh_quality_gates": True,
            "batch_recheck_count": next_count,
            "verify_loop_status": "IN_PROGRESS",
            "verify_loop_terminal": False,
        }

    if is_verify_clean(state):
        return "terminal_ok", {
            "verify_loop_status": "VERIFY_OK",
            "verify_loop_terminal": True,
            **reset_outer_batch_patch(),
        }

    from workflows.verify.pytest_recovery import resolve_pytest_recovery_action

    unresolved_cohort = _latest_cohort_unknown(state)
    if unresolved_cohort:
        recovery = resolve_pytest_recovery_action(state)
        if recovery == "ENQUEUE_HARNESS":
            repair_order = _load_repair_order(state)
            if repair_order is None:
                return "terminal_pause", {
                    "verify_loop_status": "PAUSE",
                    "verify_loop_terminal": True,
                    "owner_attention": "HARNESS_RECOVERY_NO_REPAIR_ORDER",
                    **reset_outer_batch_patch(),
                }
            harness_patch = build_harness_recovery_dispatch_patch(repair_order)
            if not harness_patch:
                return "terminal_pause", {
                    "verify_loop_status": "PAUSE",
                    "verify_loop_terminal": True,
                    "owner_attention": HARNESS_RECOVERY_NO_HARNESS_UNIT,
                    **reset_outer_batch_patch(),
                }
            return "harness_recovery", {
                **harness_patch,
                "verify_harness_recovery_attempts": int(
                    state.get("verify_harness_recovery_attempts") or 0,
                ) + 1,
                "verify_outer_route_decision": "harness_recovery",
                "verify_loop_status": "IN_PROGRESS",
                "verify_loop_terminal": False,
                **reset_dispatcher_pass_for_next_unit(),
            }
        return "terminal_pause", {
            "verify_loop_status": "PAUSE",
            "verify_loop_terminal": True,
            "owner_attention": unresolved_cohort,
            **reset_outer_batch_patch(),
        }

    iteration = int(state.get("verify_outer_iteration") or 0)
    max_iter = resolve_outer_max_iterations(state)
    if iteration >= max_iter:
        patch: dict[str, Any] = {
            "verify_loop_status": "VERIFY_LOOP_CAP",
            "verify_loop_terminal": True,
            **reset_outer_batch_patch(),
        }
        from workflows.verify.routing_log import log_gate_repair_stop

        log_gate_repair_stop(
            {**state, **patch},
            stop_kind="VERIFY_LOOP_CAP",
            source="decide_outer_route_after_unit.max_iterations",
        )
        return "terminal_cap", patch

    diagnostic = _load_diagnostic(state)
    repair_order = _load_repair_order(state)
    per_finding = resolve_verify_dispatch_mode(state) == "per_finding"
    if diagnostic is None or repair_order is None:
        return "terminal_pause", {
            "verify_loop_status": "PAUSE",
            "verify_loop_terminal": True,
            "owner_attention": (
                "REPAIR_HANDOFF_MISSING: group_error/repair_order/diagnostic "
                "did not materialize a complete repair contract; no retry "
                "or final PASS is authorized"
            ),
            **reset_outer_batch_patch(),
        }
    resolved_snapshot = _resolved_finding_ids(state)
    pending_pf = (
        pending_finding_ids(repair_order, resolved=resolved_snapshot)
        if per_finding
        else []
    )
    diagnostic_refresh_patch: dict[str, Any] = {}
    if (
        per_finding
        and repair_order.status == "SORTED"
        and not pending_pf
        and not is_verify_clean(state)
    ):
        return "final_gates", {
            "verify_loop_status": "IN_PROGRESS",
            "verify_loop_terminal": False,
            "verify_outer_batch_final_pending": True,
            "verify_outer_route_decision": "final_gates",
            "verify_outer_batch_start_failures": list(
                failure_fingerprints(state.get("quality_gate_diagnostics"))
            ),
            **diagnostic_refresh_patch,
        }
    if diagnostic is not None:
        from agents.verify.group_error.stale_skip_handoff import (
            refresh_diagnostic_after_reconciled_skip,
        )

        refreshed, diagnostic_refresh_patch = refresh_diagnostic_after_reconciled_skip(
            state,
            diagnostic,
            repair_order,
            resolved=resolved_snapshot,
        )
        if refreshed is None and per_finding and repair_order.status == "SORTED":
            return "final_gates", {
                "verify_loop_status": "IN_PROGRESS",
                "verify_loop_terminal": False,
                "verify_outer_batch_final_pending": True,
                "verify_outer_route_decision": "final_gates",
                "verify_outer_batch_start_failures": list(
                    failure_fingerprints(state.get("quality_gate_diagnostics"))
                ),
                **diagnostic_refresh_patch,
            }
        if refreshed is not None:
            diagnostic = refreshed
    handoff_ok = (
        diagnostic.status == "DIAGNOSED"
        and repair_order.status == "SORTED"
        and repair_order.ordered_units
    )
    if per_finding:
        handoff_ok = handoff_ok and bool(pending_pf)
    else:
        handoff_ok = (
            handoff_ok
            and diagnostic.repair_unit is not None
            and bool(diagnostic.repair_unit.finding_ids)
        )
    if not handoff_ok:
        from agents.verify.repair.executor_blocked_handoff import (
            maybe_executor_blocked_terminal_pause_patch,
        )

        blocked_pause = maybe_executor_blocked_terminal_pause_patch(state)
        if blocked_pause is not None:
            return "terminal_pause", blocked_pause
        return "terminal_pause", {
            "verify_loop_status": "PAUSE",
            "verify_loop_terminal": True,
            "owner_attention": (
                "REPAIR_HANDOFF_PAUSED: a failed quality gate produced no "
                "actionable repair unit; "
                f"diagnostic_status={diagnostic.status}; "
                f"diagnostic_reason={diagnostic.pause_reason or 'none'}; "
                f"repair_order_status={repair_order.status}; "
                f"repair_order_reason={repair_order.pause_reason or 'none'}; "
                "do not retry or rerun final gates as a substitute for repair"
            ),
            **reset_outer_batch_patch(),
            **diagnostic_refresh_patch,
        }

    resolved = _resolved_finding_ids(state)
    remaining_raw = list(diagnostic.remaining_queue)
    remaining, partial_pause = filter_remaining_queue(
        remaining_raw,
        repair_order,
        resolved,
        per_finding=per_finding,
    )
    if partial_pause:
        return "terminal_pause", {
            "verify_loop_status": "PAUSE",
            "verify_loop_terminal": True,
            "owner_attention": partial_pause,
            **reset_outer_batch_patch(),
        }

    if not remaining:
        return "final_gates", {
            "verify_loop_status": "IN_PROGRESS",
            "verify_loop_terminal": False,
            "verify_outer_batch_final_pending": True,
            "verify_outer_route_decision": "final_gates",
            "verify_outer_batch_start_failures": list(
                failure_fingerprints(state.get("quality_gate_diagnostics"))
            ),
        }

    files_changed = collect_batch_files_changed(state)
    next_unit = remaining[0]
    invalid = queue_invalidation_reason(
        state,
        files_changed=files_changed,
        next_unit=next_unit,
    )
    if invalid:
        failure_set = list(failure_fingerprints(state.get("quality_gate_diagnostics")))
        previous = state.get("verify_last_invalid_fingerprints")
        if (
            isinstance(previous, list)
            and previous == failure_set
            and state.get("verify_last_invalid_reason") == invalid
        ):
            return "terminal_pause", {
                "verify_loop_status": "PAUSE",
                "verify_loop_terminal": True,
                "owner_attention": (
                    "NON_CONVERGENT_INVALIDATION: same failed gate findings and "
                    f"same shared-path invalidation {invalid}; rerun prohibited"
                ),
                **reset_outer_batch_patch(),
            }
        return "full_cycle", {
            "verify_loop_status": "IN_PROGRESS",
            "verify_loop_terminal": False,
            "verify_outer_route_decision": "full_cycle",
            "verify_last_invalid_fingerprints": failure_set,
            "verify_last_invalid_reason": invalid,
            **reset_outer_batch_patch(),
        }

    unit_map = _unit_map(repair_order)
    next_ordered = unit_map.get(next_unit.unit_id)
    if next_ordered is not None and _unknown_status_for_unit(state, next_ordered):
        return "terminal_pause", {
            "verify_loop_status": "PAUSE",
            "verify_loop_terminal": True,
            "owner_attention": (
                f"cannot verify freshness for next unit {next_unit.unit_id}; blind repair blocked"
            ),
            **reset_outer_batch_patch(),
        }

    pending_after = (
        pending_finding_ids(repair_order, resolved=resolved) if per_finding else []
    )
    stall_key = (
        pending_after[0] if per_finding and pending_after else next_unit.unit_id
    )
    prev_key = str(state.get("verify_outer_batch_stall_key") or "")
    prev_count = int(state.get("verify_outer_batch_stall_count") or 0)
    stall_count = prev_count + 1 if prev_key == stall_key else 1
    if stall_count > 2:
        patch = {
            "verify_loop_status": "VERIFY_LOOP_CAP",
            "verify_loop_terminal": True,
            "owner_attention": f"no progress on unit {stall_key}",
            **reset_outer_batch_patch(),
        }
        from workflows.verify.routing_log import log_gate_repair_stop

        log_gate_repair_stop(
            {**state, **patch},
            stop_kind="VERIFY_LOOP_CAP",
            source="decide_outer_route_after_unit.unit_stall",
        )
        return "terminal_cap", patch

    shrunk = shrink_repair_order(repair_order, remaining)
    analysis_raw = state.get("analysis_result")
    analysis = (
        GroupErrorResult.model_validate(analysis_raw)
        if isinstance(analysis_raw, dict)
        else None
    )
    next_diagnostic = build_anchor_only_diagnostic(
        shrunk,
        analysis=analysis,
        resolved=resolved,
        per_finding=per_finding,
    )
    return "next_unit", {
        "verify_loop_status": "IN_PROGRESS",
        "verify_loop_terminal": False,
        "verify_outer_batch_active": True,
        "verify_outer_route_decision": "next_unit",
        "verify_outer_batch_stall_key": stall_key,
        "verify_outer_batch_stall_count": stall_count,
        "repair_order_result": shrunk.model_dump(mode="json"),
        "diagnostic_result": next_diagnostic.model_dump(mode="json"),
        **reset_dispatcher_pass_for_next_unit(),
    }


async def verify_reset_repair_batch(state: GraphState) -> dict[str, Any]:
    """После провала финальных gates: сброс batch и +1 outer iteration для cap."""
    from workflows.verify.outer_loop import resolve_outer_max_iterations

    iteration = int(state.get("verify_outer_iteration") or 0) + 1
    max_iter = resolve_outer_max_iterations(state)
    base = {
        **reset_outer_batch_patch(),
        "verify_outer_iteration": iteration,
    }
    baseline = state.get("verify_outer_batch_start_failures")
    current = set(failure_fingerprints(state.get("quality_gate_diagnostics")))
    if not isinstance(baseline, list) or not baseline or not current:
        detail = (
            "COHORT_PROGRESS_UNPROVEN: missing or empty full-gate failure "
            "fingerprint before/after repair; cannot repeat safely"
        )
        return _cohort_restart_or_pause(state, detail=detail)
    before = set(baseline)
    # Strict set reduction is the convergence predicate. A sibling regression,
    # unchanged set, or different failures of equal size stops immediately.
    if not current < before:
        detail = (
            "COHORT_NO_CONVERGENCE: full-gate findings did not strictly "
            f"decrease; before={len(before)}, after={len(current)}, "
            f"new={sorted(current - before)[:8]}, "
            f"remaining={sorted(current & before)[:8]}"
        )
        return _cohort_pause_no_progress(state, detail=detail)
    if iteration >= max_iter:
        patch = {
            **base,
            "verify_loop_status": "VERIFY_LOOP_CAP",
            "verify_loop_terminal": True,
        }
        from workflows.verify.routing_log import log_gate_repair_stop

        log_gate_repair_stop(
            {**state, **patch},
            stop_kind="VERIFY_LOOP_CAP",
            source="verify_reset_repair_batch",
        )
        return patch
    return {
        **base,
        "verify_loop_status": "IN_PROGRESS",
        "verify_loop_terminal": False,
        "verify_cohort_restart_count": 0,
        "verify_post_reset_route": None,
    }


verify_reset_repair_batch_node = verify_reset_repair_batch


async def verify_mark_batch_started_node(_state: GraphState) -> dict[str, Any]:
    """Отметить начало batch после первого diagnostic (GroupError/RepairOrder уже выполнены)."""
    from agents.verify.dispatch_mode import resolve_verify_dispatch_mode

    mode = resolve_verify_dispatch_mode(_state)
    return {"verify_outer_batch_active": True, "verify_dispatch_mode": mode}
