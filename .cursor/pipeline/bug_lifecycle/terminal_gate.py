"""Terminal BUG_BATCH_CLEAR gate (provenance, identity, membership, unresolved)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .bug_team_evidence import validate_bug_team_packet, validate_harvest_bundle
from .disposition_route import (
    ROUTE_GLOBAL_BLOCKER,
    ROUTE_MUST_FIX_REPAIR,
    ROUTE_MIXED_SPLIT,
    ROUTE_NON_REPAIR,
    ROUTE_SECURITY_PROCESS,
    ROUTE_TECH_DEBT,
    read_disposition,
)
from .green_authority import validate_green_file_bound

MATERIALIZE_DIR = Path("opsx/runs/bug-cases/materialize")
UNRESOLVED_RE = re.compile(r"^STATUS:\s*(?!closed_fixed)", re.I | re.M)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _terminal_materialize_path(
    repo_root: Path,
    mission_id: str,
    finding_id: str,
    terminal_status: str,
) -> Path:
    return (repo_root / MATERIALIZE_DIR / f"{terminal_status}-{mission_id}-{finding_id}.json").resolve()


def _verify_repair_terminal(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    item: dict[str, Any],
    production: bool,
) -> list[str]:
    violations: list[str] = []
    finding_id = item["finding_id"]
    case_id = item["case_id"]
    work_item_id = str(item.get("work_item_id") or "")
    close_path = (repo_root / MATERIALIZE_DIR / f"close-{mission_id}-{finding_id}.json").resolve()
    payload = _load_json(close_path)
    if payload is None:
        violations.append(f"{finding_id}:missing_close_receipt")
        return violations
    if str(payload.get("execution_id") or "") != execution_id:
        violations.append(f"{finding_id}:stale_execution")
    if str(payload.get("head") or "") != head:
        violations.append(f"{finding_id}:stale_head")
    if str(payload.get("mission_id") or "") != mission_id:
        violations.append(f"{finding_id}:membership")
    if str(payload.get("case_id") or "") != case_id:
        violations.append(f"{finding_id}:case_membership")
    if str(payload.get("terminal_status") or "") != "closed_fixed":
        violations.append(f"{finding_id}:not_closed_fixed")
    green_rel = str(payload.get("green_evidence") or "")
    green = repo_root / green_rel if green_rel else (
        repo_root / "opsx/runs/bug-cases" / case_id / "results" / f"GREEN-{finding_id}.md"
    )
    ok, reason = validate_green_file_bound(
        green,
        case_id=case_id,
        finding_id=finding_id,
        execution_id=execution_id,
        production=production,
    )
    if not ok:
        violations.append(f"{finding_id}:{reason}")
    packet = (
        repo_root
        / "opsx/runs/bug-cases"
        / case_id
        / "results"
        / f"BUG-TEAM-{finding_id}.json"
    )
    packet_body = _load_json(packet)
    if packet_body is None:
        violations.append(f"{finding_id}:missing_bug_team_packet")
    else:
        ok, reason = validate_bug_team_packet(
            packet_body,
            case_id=case_id,
            finding_id=finding_id,
            execution_id=execution_id,
            head=head,
            work_item_id=work_item_id,
            production=production,
        )
        if not ok:
            violations.append(f"{finding_id}:{reason}")
    if production:
        ok, reason = validate_harvest_bundle(
            repo_root,
            mission_id=mission_id,
            execution_id=execution_id,
            head=head,
            case_id=case_id,
            finding_id=finding_id,
            work_item_id=work_item_id,
            production=True,
        )
        if not ok:
            violations.append(f"{finding_id}:{reason}")
    return violations


def _verify_non_repair_terminal(
    repo_root: Path,
    *,
    mission_id: str,
    finding_id: str,
    terminal_status: str,
) -> list[str]:
    path = _terminal_materialize_path(repo_root, mission_id, finding_id, terminal_status)
    if not path.is_file():
        return [f"{finding_id}:missing_{terminal_status}_materialize"]
    payload = _load_json(path)
    if payload is None or str(payload.get("terminal_status") or "") != terminal_status:
        return [f"{finding_id}:invalid_{terminal_status}_materialize"]
    return []


def _verify_tech_debt_handoff(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
) -> list[str]:
    handoff = repo_root / "opsx/runs/orch-missions" / mission_id / "evolution-handoff.json"
    if not handoff.is_file():
        return ["tech_debt:missing_evolution_handoff"]
    payload = _load_json(handoff)
    if payload is None or str(payload.get("execution_id") or "") != execution_id:
        return ["tech_debt:stale_evolution_handoff"]
    return []


def verify_terminal_gate(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    work_items: list[dict[str, Any]],
    production: bool,
) -> dict[str, Any]:
    """Mission-scoped S10 predicate для всех классов disposition."""

    missing: list[str] = []
    stale: list[str] = []
    provenance: list[str] = []
    unresolved: list[str] = []
    membership: list[str] = []
    global_blockers: list[str] = []

    for item in work_items:
        finding_id = item["finding_id"]
        case_id = item["case_id"]
        route = str(item.get("route") or ROUTE_MUST_FIX_REPAIR)
        finding_path = repo_root / "opsx/runs/bug-cases" / case_id / "findings" / f"{finding_id}.md"
        if finding_path.is_file():
            text = finding_path.read_text(encoding="utf-8")
            if f"MISSION_ID: {mission_id}" not in text and mission_id:
                membership.append(finding_id)
            disp = read_disposition(repo_root, case_id, finding_id)
            if disp == "UNRESOLVED":
                unresolved.append(finding_id)

        if route in {ROUTE_MUST_FIX_REPAIR, ROUTE_MIXED_SPLIT}:
            for violation in _verify_repair_terminal(
                repo_root,
                mission_id=mission_id,
                execution_id=execution_id,
                head=head,
                item=item,
                production=production,
            ):
                if ":missing_close" in violation or "missing_close" in violation:
                    missing.append(finding_id)
                elif "stale" in violation:
                    stale.append(finding_id)
                else:
                    provenance.append(violation)
            continue

        if route == ROUTE_NON_REPAIR:
            disp = str(item.get("disposition") or "")
            terminal = "false_positive" if disp.startswith("FALSE_POSITIVE") else "deferred"
            provenance.extend(
                _verify_non_repair_terminal(
                    repo_root,
                    mission_id=mission_id,
                    finding_id=finding_id,
                    terminal_status=terminal,
                )
            )
            continue

        if route == ROUTE_SECURITY_PROCESS:
            provenance.extend(
                _verify_non_repair_terminal(
                    repo_root,
                    mission_id=mission_id,
                    finding_id=finding_id,
                    terminal_status="security_process_route",
                )
            )
            continue

        if route == ROUTE_TECH_DEBT:
            provenance.extend(_verify_tech_debt_handoff(repo_root, mission_id=mission_id, execution_id=execution_id))
            continue

        if route == ROUTE_GLOBAL_BLOCKER:
            global_blockers.append(finding_id)
            provenance.extend(
                _verify_non_repair_terminal(
                    repo_root,
                    mission_id=mission_id,
                    finding_id=finding_id,
                    terminal_status="global_release_blocker",
                )
            )
            continue

        missing.append(finding_id)

    if global_blockers:
        return {
            "BUG_BATCH_CLEAR": "NO",
            "STATUS": "BLOCKED_GLOBAL_BLOCKER",
            "GLOBAL_BLOCKERS": global_blockers,
            "PROVENANCE_VIOLATIONS": provenance,
            "MISSING": missing,
            "STALE_IDENTITY": stale,
            "UNRESOLVED_DISPOSITION": unresolved,
            "MEMBERSHIP_VIOLATIONS": membership,
        }

    if missing or stale or provenance or unresolved or membership:
        return {
            "BUG_BATCH_CLEAR": "NO",
            "STATUS": "ACTIVE",
            "MISSING": missing,
            "STALE_IDENTITY": stale,
            "PROVENANCE_VIOLATIONS": provenance,
            "UNRESOLVED_DISPOSITION": unresolved,
            "MEMBERSHIP_VIOLATIONS": membership,
        }
    finding_ids = [w["finding_id"] for w in work_items]
    return {
        "BUG_BATCH_CLEAR": "YES",
        "STATUS": "BUG_BATCH_CLEAR",
        "MISSION_ID": mission_id,
        "VERIFIED_FINDINGS": finding_ids,
        "TERMINAL_GATE": "PASS",
    }
