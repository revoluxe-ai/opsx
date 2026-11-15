"""Production S4: /opsx-bug-team dispatch or consume host-harvested terminal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bug_team_evidence import validate_bug_team_packet, validate_harvest_bundle
from .green_authority import validate_green_file_bound
from .bug_team_transport import record_dispatch, write_execution_terminal
from .worktree_ownership import load_ownership_receipt
from .hermetic_boundary import hermetic_execution_enabled
from .hermetic_test_executor import apply_bug_team_terminal

WRITER = "/opsx-bug-team"


def _packet_path(repo_root: Path, case_id: str, finding_id: str) -> Path:
    return (
        repo_root
        / "opsx"
        / "runs"
        / "bug-cases"
        / case_id
        / "results"
        / f"BUG-TEAM-{finding_id}.json"
    )


def consume_terminal_if_present(
    repo_root: Path,
    request: dict[str, Any],
    *,
    case_id: str,
    finding_id: str,
    production: bool,
    work_item_id: str = "",
    head: str = "",
) -> dict[str, Any] | None:
    path = _packet_path(repo_root, case_id, finding_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"STATUS": "FAIL_CLOSED", "REASON": "invalid_bug_team_packet"}
    execution_id = str(request.get("execution_id") or "")
    mission_id = str(request.get("mission") or "")
    bound_head = head or str(request.get("head") or "")
    wi = work_item_id or str(payload.get("work_item_id") or "")
    ok, reason = validate_bug_team_packet(
        payload,
        case_id=case_id,
        finding_id=finding_id,
        execution_id=execution_id,
        head=bound_head,
        work_item_id=wi,
        production=production,
    )
    if not ok:
        return {"STATUS": "FAIL_CLOSED", "REASON": reason}
    green = (
        repo_root
        / "opsx"
        / "runs"
        / "bug-cases"
        / case_id
        / "results"
        / f"GREEN-{finding_id}.md"
    )
    ok, reason = validate_green_file_bound(
        green,
        case_id=case_id,
        finding_id=finding_id,
        execution_id=execution_id,
        production=production,
    )
    if not ok:
        return {"STATUS": "FAIL_CLOSED", "REASON": reason}
    if production:
        ok, reason = validate_harvest_bundle(
            repo_root,
            mission_id=mission_id,
            execution_id=execution_id,
            head=bound_head,
            case_id=case_id,
            finding_id=finding_id,
            work_item_id=wi,
            production=True,
        )
        if not ok:
            return {"STATUS": "FAIL_CLOSED", "REASON": reason}
    provenance = str(payload.get("PROVENANCE") or "HOST_BUG_TEAM")
    write_execution_terminal(
        repo_root,
        mission_id=mission_id,
        execution_id=execution_id,
        head=bound_head,
        source=provenance,
    )
    return {"STATUS": "READY_FOR_CLOSURE", "CASE_ID": case_id, "FINDING_ID": finding_id}


def run_s4(
    repo_root: Path,
    request: dict[str, Any],
    *,
    case_id: str,
    finding_id: str,
    plan_id: str,
    work_item_id: str,
    production: bool,
) -> dict[str, Any]:
    consumed = consume_terminal_if_present(
        repo_root,
        request,
        case_id=case_id,
        finding_id=finding_id,
        production=production,
        work_item_id=work_item_id,
    )
    if consumed is not None:
        return consumed
    if hermetic_execution_enabled(repo_root):
        return apply_bug_team_terminal(
            repo_root,
            request,
            case_id=case_id,
            finding_id=finding_id,
            plan_id=plan_id,
            work_item_id=work_item_id,
        )
    mission_id = str(request.get("mission") or "")
    ownership = load_ownership_receipt(repo_root, mission_id, work_item_id) if work_item_id else None
    scope = list((ownership or {}).get("WORK_ITEM_SCOPE_PATHS") or [])
    partial = list((ownership or {}).get("PREEXISTING_PARTIAL_REPAIR_PATHS") or [])
    forbidden = list((ownership or {}).get("FORBIDDEN_PATHS") or [])
    member_ids = list(request.get("member_ids") or []) if isinstance(request.get("member_ids"), list) else []
    dispatch = record_dispatch(
        repo_root,
        mission_id=mission_id,
        execution_id=str(request.get("execution_id") or ""),
        head=str(request.get("head") or ""),
        case_id=case_id,
        finding_id=finding_id,
        plan_id=plan_id,
        work_item_id=work_item_id,
        work_item_scope_paths=scope,
        preexisting_partial_repair_paths=partial,
        forbidden_paths=forbidden,
        member_ids=member_ids,
        disposition_id=str(request.get("disposition_id") or finding_id),
    )
    if dispatch.get("STATUS") == "FAIL_CLOSED":
        return dispatch
    liveness = str(request.get("worker_liveness") or "").upper()
    if production and liveness == "UNKNOWN":
        return {
            "STATUS": "PAUSE",
            "REASON": "STOP_UNCONFIRMED_worker_liveness",
            "MISSION_ID": mission_id,
            "FINDING_ID": finding_id,
            "DISPATCH_ID": dispatch.get("DISPATCH_ID"),
        }
    if dispatch.get("IDEMPOTENT") == "YES":
        replay = consume_terminal_if_present(
            repo_root,
            request,
            case_id=case_id,
            finding_id=finding_id,
            production=production,
            work_item_id=work_item_id,
        )
        if replay is not None:
            return replay
    return {
        **dispatch,
        "STATUS": "DISPATCH_REQUIRED",
        "NEXT_COMMAND": WRITER,
        "BUG_TEAM_DISPATCH": "PENDING",
        "CASE_ID": case_id,
        "FINDING_ID": finding_id,
        "plan": plan_id,
        "work_item": work_item_id,
        "REASON": "authoritative_bug_team_receipt_required",
    }
