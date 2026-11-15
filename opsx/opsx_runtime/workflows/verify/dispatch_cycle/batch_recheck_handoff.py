"""Выход из dispatch_cycle: discovery-style repair, затем один FULL_SUITE.

В режиме ``per_finding`` (default) после COMMITTED finding полный ``quality_gates``
запрашивается только когда в ``repair_order`` не осталось pending findings.
Пока очередь не пуста — handoff только продвигает dispatcher (без ``verify_batch_recheck_required``).
"""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.dispatch_mode import resolve_verify_dispatch_mode
from agents.verify.dispatcher.agent import dispatcher_record_outcome
from agents.verify.dispatcher.flatten_order import pending_finding_ids
from workflows.verify.outer_batch import _load_repair_order, _resolved_finding_ids


def _committed_finding_resolved_patch(
    state: GraphState,
    *,
    finding_id: str,
    commit_status: str,
    commit_outcome: dict[str, Any] | None,
) -> dict[str, Any]:
    """После COMMITTED пометить finding resolved и сбросить outer stall (META batch recheck)."""
    status = commit_status or (
        str(commit_outcome.get("status") or "") if isinstance(commit_outcome, dict) else ""
    )
    if status != "COMMITTED" or not finding_id:
        return {}
    raw = state.get("dispatcher_resolved_finding_ids")
    resolved = {str(x) for x in raw if x} if isinstance(raw, list) else set()
    resolved.add(finding_id)
    return {
        "dispatcher_resolved_finding_ids": sorted(resolved),
        "verify_outer_batch_stall_key": None,
        "verify_outer_batch_stall_count": 0,
    }


def _pending_findings_after_commit(
    state: GraphState,
    resolved_patch: dict[str, Any],
) -> list[str]:
    """Finding ids, которые ещё нужно починить после текущего COMMITTED."""
    repair_order = _load_repair_order(state)
    if repair_order is None:
        return []
    merged: GraphState = {**state, **resolved_patch}  # type: ignore[typeddict-item]
    resolved = _resolved_finding_ids(merged)
    return pending_finding_ids(repair_order, resolved=resolved)


def _requires_full_suite_recheck(
    state: GraphState,
    resolved_patch: dict[str, Any],
) -> bool:
    """Нужен ли немедленный FULL_SUITE (legacy ``repair_unit`` — всегда после fix)."""
    if resolve_verify_dispatch_mode(state) == "repair_unit":
        return True
    return not _pending_findings_after_commit(state, resolved_patch)


async def run_batch_recheck_handoff(state: GraphState) -> dict[str, Any]:
    """Зафиксировать repair outcome и запросить parent batch recheck."""
    repair_outcome = state.get("verify_finding_repair_outcome")
    commit_outcome = state.get("verify_finding_commit_outcome")
    item = state.get("dispatcher_current_item")
    finding_id = (
        str(item.get("finding_id") or "")
        if isinstance(item, dict)
        else ""
    )
    commit_status = str(state.get("commit_status") or "")
    co = commit_outcome if isinstance(commit_outcome, dict) else None
    resolved_patch = _committed_finding_resolved_patch(
        state,
        finding_id=finding_id,
        commit_status=commit_status,
        commit_outcome=co,
    )
    needs_full_suite = _requires_full_suite_recheck(state, resolved_patch)
    pending_after = _pending_findings_after_commit(state, resolved_patch)
    action = (
        "BATCH_RECHECK_REQUIRED"
        if needs_full_suite
        else "BATCH_RECHECK_DEFERRED_PENDING_FINDINGS"
    )
    outcome: dict[str, Any] = {
        "schema_version": "1.0",
        "agent": "batch_recheck_handoff",
        "finding_id": finding_id or None,
        "repair": repair_outcome,
        "commit": commit_outcome,
        "commit_status": state.get("commit_status"),
        "action": action,
        "pending_finding_ids": pending_after,
    }
    advance = await dispatcher_record_outcome(
        state,
        workflow_kind="test",
        outcome=outcome,
    )
    patch: dict[str, Any] = {
        **advance,
        **resolved_patch,
        "verify_finding_finalize_outcome": outcome,
    }
    if needs_full_suite:
        patch["verify_batch_recheck_required"] = True
    return patch
