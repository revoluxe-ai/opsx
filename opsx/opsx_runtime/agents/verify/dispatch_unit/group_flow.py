"""Узлы LangGraph: prepare causal group, verify, finalize repair unit."""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.dispatch_unit.queue_items import (
    finding_item_from_group,
    is_repair_unit_item,
)
from agents.verify.dispatch_unit.schemas import CausalRepairGroup, FindingMemberStatus
from agents.verify.dispatch_unit.repro_cohort import reproduction_cohort, cohort_identity
from agents.verify.dispatch_unit.validation import groups_from_triage_dict
from agents.verify.dispatch_unit.verify_group import (
    verify_findings_already_absent,
    verify_group_findings,
)
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.pytest_run_health import is_harness_blocker
from agents.verify.dispatcher.agent import _queue_slot, dispatcher_record_outcome


def _resolved_set(state: GraphState) -> set[str]:
    raw = state.get("dispatcher_resolved_finding_ids")
    if not isinstance(raw, list):
        return set()
    return {str(x) for x in raw if x}


def _append_resolved(patch: dict[str, Any], state: GraphState, finding_ids: list[str]) -> None:
    current = _resolved_set(state)
    current.update(finding_ids)
    patch["dispatcher_resolved_finding_ids"] = sorted(current)


def _findings_by_id(state: GraphState) -> dict[str, Any]:
    raw = state.get("analysis_result")
    if not isinstance(raw, dict):
        return {}
    analysis = GroupErrorResult.model_validate(raw)
    return {f.finding_id: f for f in analysis.findings}


def dispatch_harness_fallback_authorized(state: GraphState) -> bool:
    """Harness dispatch только если product-scope execute ещё не завершал scope."""
    finished = state.get("pytest_execute_finished_scopes")
    if isinstance(finished, list) and finished:
        return False
    return True


def _pending_has_harness_collection(state: GraphState, pending: list[str]) -> bool:
    by_id = _findings_by_id(state)
    return any(
        is_harness_blocker(getattr(by_id.get(fid), "repair_workflow", None))
        for fid in pending
        if fid in by_id
    )


def _statuses_to_resolved_ids(statuses: list[FindingMemberStatus]) -> list[str]:
    return [
        s.finding_id
        for s in statuses
        if s.status in ("RESOLVED", "ALREADY_RESOLVED")
    ]


async def prepare_causal_group(state: GraphState) -> dict[str, Any]:
    """Выбрать текущую причинную группу и synthetic item для repair hop."""
    _, _, slot = _queue_slot(state)
    unit_item = slot
    if not is_repair_unit_item(unit_item):
        raise ValueError("prepare_causal_group requires repair_unit queue slot")

    assert isinstance(unit_item, dict)
    groups_raw = list(state.get("dispatch_causal_groups") or [])
    groups = [CausalRepairGroup.model_validate(g) for g in groups_raw]
    index = int(state.get("dispatcher_group_index") or 0)
    resolved = _resolved_set(state)

    while index < len(groups):
        group = groups[index]
        pending = [fid for fid in group.finding_ids if fid not in resolved]
        if not pending:
            index += 1
            continue
        break
    else:
        return {"dispatcher_route": "unit_finalize", "dispatcher_group_index": index}

    group = groups[index]
    pending = [fid for fid in group.finding_ids if fid not in resolved]
    if _pending_has_harness_collection(state, pending):
        harness_statuses = verify_group_findings(
            state, finding_ids=pending, files_changed=[],
        )
        return {
            "dispatcher_current_group": group.model_dump(mode="json"),
            "dispatcher_group_index": index,
            "dispatcher_group_phase": "verify",
            "dispatch_group_verify_result": {
                "group_id": group.group_id,
                "member_statuses": [s.model_dump(mode="json") for s in harness_statuses],
                "skipped_repair": True,
                "harness_collection_skip": True,
            },
        }
    cohort = (
        reproduction_cohort(state.get("analysis_result"), finding_ids=pending)
        if group.classification == "TEST" else None
    )
    if cohort and cohort.get("test_nodes"):
        source = state.get("analysis_result") or {}
        identity = source.get("analysis_identity") if isinstance(source, dict) else {}
        identity = identity if isinstance(identity, dict) else {}
        cohort["cohort_id"] = cohort_identity(
            run_id=(
                f"{identity.get('execution_id') or state.get('run_uuid') or 'unknown'}:"
                f"{identity.get('revision') or 'unbound'}"
            ),
            test_nodes=list(cohort["test_nodes"]),
        )
    if cohort and cohort["basis"] == "COHORT_TOO_BROAD":
        return {
            "dispatcher_current_group": group.model_dump(mode="json"),
            "dispatcher_group_index": index,
            "dispatcher_group_phase": "verify",
            "dispatch_group_verify_result": {
                "group_id": group.group_id,
                "member_statuses": [
                    FindingMemberStatus(
                        finding_id=fid, status="UNKNOWN",
                        detail=f"COHORT_TOO_BROAD: {cohort['reason']}",
                    ).model_dump(mode="json") for fid in pending
                ],
                "skipped_repair": True,
            },
        }
    verification_ids = (
        list(cohort["finding_ids"])
        if cohort and cohort["finding_ids"] else pending
    )
    pre_kwargs = (
        {"pytest_modules": list(cohort["test_modules"])}
        if cohort and cohort["test_modules"] else {}
    )
    pre_statuses = verify_findings_already_absent(
        state, finding_ids=verification_ids, **pre_kwargs,
    )
    if pre_statuses and all(s.status == "ALREADY_RESOLVED" for s in pre_statuses):
        return {
            "dispatcher_current_group": group.model_dump(mode="json"),
            "dispatcher_group_index": index,
            "dispatcher_group_phase": "verify",
            "dispatch_group_verify_result": {
                "group_id": group.group_id,
                "member_statuses": [s.model_dump(mode="json") for s in pre_statuses],
                "skipped_repair": True,
            },
        }

    group_item = finding_item_from_group(unit_item, group)
    if cohort and cohort["test_nodes"]:
        group_item["reproduction_cohort"] = cohort
    route = group.repair_route
    triage_patch: dict[str, Any] = {}
    if route == "test_harness":
        triage_patch["verify_dispatch_path"] = "test_harness"
    elif route in ("test", "personal_timeout"):
        triage_patch["test_triage_result"] = {
            "finding_id": group_item.get("finding_id"),
            "classification": group.classification,
            "rationale": group.rationale,
        }
        triage_patch["verify_dispatch_path"] = "test"
    else:
        triage_patch["static_triage_result"] = {
            "finding_id": group_item.get("finding_id"),
            "classification": group.classification,
            "rationale": group.rationale,
        }
        triage_patch["verify_dispatch_path"] = "static"

    return {
        **triage_patch,
        "dispatcher_current_group": group.model_dump(mode="json"),
        "dispatcher_current_item": group_item,
        "dispatcher_group_index": index,
        "dispatcher_group_phase": "repair",
        "dispatcher_route": "group_repair_ready",
    }


def group_repair_route_edge(state: GraphState) -> str:
    """После prepare: repair hop или verify если repair не нужен."""
    verify_raw = state.get("dispatch_group_verify_result")
    if isinstance(verify_raw, dict) and verify_raw.get("skipped_repair"):
        return "group_verify"
    group_raw = state.get("dispatcher_current_group")
    if not isinstance(group_raw, dict):
        return "group_verify"
    repair_route = group_raw.get("repair_route")
    if repair_route == "personal_timeout":
        return "pytest_personal_timeout"
    if repair_route == "test_harness":
        if not dispatch_harness_fallback_authorized(state):
            return "group_verify"
        return "test_harness_repair"
    classification = group_raw.get("classification")
    if classification == "LOGIC":
        return "logic_bug_repair"
    if classification == "TEST":
        return "test_repair"
    if classification == "TYPING":
        return "typing_repair"
    return "group_verify"


async def run_group_verify_node(state: GraphState) -> dict[str, Any]:
    """Проверить все findings текущей группы после repair (или skip)."""
    group_raw = state.get("dispatcher_current_group")
    if not isinstance(group_raw, dict):
        group = None
    else:
        group = CausalRepairGroup.model_validate(group_raw)

    existing = state.get("dispatch_group_verify_result")
    files: list[str] = []
    if isinstance(existing, dict) and existing.get("skipped_repair"):
        statuses = [
            FindingMemberStatus.model_validate(s)
            for s in (existing.get("member_statuses") or [])
            if isinstance(s, dict)
        ]
    else:
        fix = state.get("verify_finding_fix")
        if isinstance(fix, dict) and isinstance(fix.get("files_changed"), list):
            files = [str(x) for x in fix["files_changed"]]
        item = state.get("dispatcher_current_item") or {}
        cohort = item.get("reproduction_cohort") if isinstance(item, dict) else None
        finding_ids = (
            list(cohort["finding_ids"])
            if isinstance(cohort, dict) and cohort.get("finding_ids")
            else list(group.finding_ids) if group is not None
            else list(item.get("group_finding_ids") or [])
        )
        kwargs = (
            {"pytest_modules": list(cohort["test_modules"])}
            if isinstance(cohort, dict) and cohort.get("test_modules") else {}
        )
        statuses = verify_group_findings(
            state, finding_ids=finding_ids, files_changed=files, **kwargs,
        )

    resolved_now = _statuses_to_resolved_ids(statuses)
    patch: dict[str, Any] = {
        "dispatch_group_verify_result": {
            "group_id": group.group_id if group else None,
            "files_changed": files,
            "member_statuses": [s.model_dump(mode="json") for s in statuses],
            "reproduction_cohort": (
                (state.get("dispatcher_current_item") or {}).get("reproduction_cohort")
                if isinstance(state.get("dispatcher_current_item"), dict) else None
            ),
        },
        "dispatcher_group_phase": "repair",
        "verify_finding_fix": None,
        "verify_finding_repair_outcome": state.get("verify_finding_repair_outcome"),
    }
    _append_resolved(patch, {**state, **patch}, resolved_now)

    index = int(state.get("dispatcher_group_index") or 0) + 1
    patch["dispatcher_group_index"] = index
    patch["dispatcher_group_phase"] = "repair"

    groups_count = len(state.get("dispatch_causal_groups") or [])
    if index >= groups_count:
        patch["dispatcher_route"] = "unit_finalize"
    else:
        patch["dispatcher_route"] = "group_repair"
    return patch


async def finalize_repair_unit_pass(state: GraphState) -> dict[str, Any]:
    """Завершить repair unit: outcome + сброс unit-local state + advance pass."""
    _, pass_index, slot = _queue_slot(state)
    unit_item = slot if is_repair_unit_item(slot) else state.get("dispatcher_current_item")
    triage_raw = state.get("dispatch_unit_triage_result")
    # A PAUSE result has no causal groups and must not be parsed as GROUPED.
    # It is still a terminal evidence item for the outer convergence gate.
    triage_is_paused = (
        isinstance(triage_raw, dict) and triage_raw.get("status") == "PAUSE"
    )
    groups = (
        groups_from_triage_dict(triage_raw)
        if isinstance(triage_raw, dict) and not triage_is_paused else []
    )

    triage_pause = (
        str(triage_raw.get("pause_reason") or "causal grouping unresolved")
        if isinstance(triage_raw, dict) and triage_raw.get("status") == "PAUSE"
        else None
    )
    verification = state.get("dispatch_group_verify_result")
    if triage_pause:
        # A PAUSE during causal grouping is not a completed repair unit.
        verification = {
            "member_statuses": [
                FindingMemberStatus(
                    finding_id=str(fid), status="UNKNOWN",
                    detail=f"GROUP_TRIAGE_PAUSE: {triage_pause}",
                ).model_dump(mode="json")
                for fid in (
                    (unit_item or {}).get("finding_ids") or []
                    if isinstance(unit_item, dict) else []
                )
            ],
        }

    outcome: dict[str, Any] = {
        "schema_version": "1.0",
        "kind": "repair_unit",
        "repair_unit_key": (unit_item or {}).get("repair_unit_key")
        if isinstance(unit_item, dict)
        else None,
        "triage": triage_raw,
        "causal_groups": [g.model_dump(mode="json") for g in groups],
        "resolved_finding_ids": sorted(_resolved_set(state)),
        "verify_results": verification,
    }

    advance = await dispatcher_record_outcome(
        state,
        workflow_kind="repair_unit",
        outcome=outcome,
    )
    reset_unit: dict[str, Any] = {
        "dispatcher_unit_triage_complete": False,
        "dispatch_causal_groups": [],
        "dispatcher_group_index": 0,
        "dispatcher_group_phase": "repair",
        "dispatch_unit_triage_result": None,
        "dispatch_group_verify_result": None,
        "dispatcher_current_group": None,
    }
    return {**advance, **reset_unit, "verify_finding_finalize_outcome": outcome}
