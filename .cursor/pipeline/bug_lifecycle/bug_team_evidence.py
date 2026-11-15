"""Authoritative Bug Team harvest evidence (provenance, identity, terminality)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .bug_team_transport import harvest_terminal_marker_path

AUTHORIZED_PACKET_PROVENANCE = frozenset(
    {
        "HOST_BUG_TEAM",
        "runtime.bug_team_host.dispatch_opsx_bug_team",
    }
)
AUTHORIZED_GREEN_PROVENANCE = frozenset(
    {
        "HOST_BUG_TEAM",
        "runtime.bug_team_host.dispatch_opsx_bug_team",
    }
)
HERMETIC_PROVENANCE = "HERMETIC_TEST_EXECUTOR"

WORKER_EXECUTION_RE = re.compile(r"^WORKER_EXECUTION_ID:\s*(\S+)\s*$", re.I | re.M)
GREEN_PROVENANCE_RE = re.compile(r"^PROVENANCE:\s*(\S+)\s*$", re.I | re.M)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def load_execution_terminal(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
) -> dict[str, Any] | None:
    marker = harvest_terminal_marker_path(repo_root, mission_id, execution_id)
    payload = _load_json(marker)
    if payload is None:
        return None
    if str(payload.get("execution_terminal") or "") != "CONFIRMED":
        return None
    return payload


def validate_execution_terminal(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    allowed_sources: frozenset[str] | None = None,
) -> tuple[bool, str]:
    payload = load_execution_terminal(repo_root, mission_id=mission_id, execution_id=execution_id)
    if payload is None:
        return False, "missing_execution_terminal_marker"
    if str(payload.get("mission_id") or "") != mission_id:
        return False, "terminal_mission_mismatch"
    if str(payload.get("execution_id") or "") != execution_id:
        return False, "terminal_execution_mismatch"
    if head and str(payload.get("head") or "") != head:
        return False, "terminal_head_mismatch"
    source = str(payload.get("source") or "")
    if allowed_sources is not None and source not in allowed_sources:
        return False, "terminal_source_untrusted"
    return True, "ok"


def validate_green_binding(
    path: Path,
    *,
    case_id: str,
    finding_id: str,
    execution_id: str,
    production: bool,
) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing_green_file"
    text = path.read_text(encoding="utf-8")
    if not re.search(r"^STATUS:\s*GREEN_CONFIRMED\s*$", text, re.I | re.M):
        return False, "green_not_confirmed"
    for field in ("TEST_ID:", "BUG_CASE_ID:", "FINDING_ID:", "TEST_CONTENT_SHA256:"):
        if field not in text:
            return False, f"missing_{field.rstrip(':').lower()}"
    if f"BUG_CASE_ID: {case_id}" not in text:
        return False, "green_case_mismatch"
    if f"FINDING_ID: {finding_id}" not in text:
        return False, "green_finding_mismatch"
    match = WORKER_EXECUTION_RE.search(text)
    if not match or match.group(1) != execution_id:
        return False, "green_execution_binding_missing"
    prov_match = GREEN_PROVENANCE_RE.search(text)
    provenance = prov_match.group(1) if prov_match else ""
    if production:
        if provenance in {"", HERMETIC_PROVENANCE}:
            return False, "synthetic_green_provenance"
        if provenance not in AUTHORIZED_GREEN_PROVENANCE:
            return False, "green_provenance_unauthorized"
    elif provenance == HERMETIC_PROVENANCE:
        return True, "ok"
    if production and provenance not in AUTHORIZED_GREEN_PROVENANCE:
        return False, "green_provenance_unauthorized"
    return True, "ok"


def validate_bug_team_packet(
    payload: dict[str, Any],
    *,
    case_id: str,
    finding_id: str,
    execution_id: str,
    head: str,
    work_item_id: str,
    production: bool,
) -> tuple[bool, str]:
    if str(payload.get("CLOSURE_READINESS") or "") != "READY_FOR_CLOSURE":
        return False, "not_ready_for_closure"
    provenance = str(payload.get("PROVENANCE") or "")
    if production:
        if provenance in {"", HERMETIC_PROVENANCE}:
            return False, "synthetic_or_missing_bug_team_provenance"
        if provenance not in AUTHORIZED_PACKET_PROVENANCE:
            return False, "packet_provenance_unauthorized"
    elif provenance == HERMETIC_PROVENANCE:
        pass
    if str(payload.get("execution_id") or "") != execution_id:
        return False, "stale_bug_team_execution"
    if str(payload.get("head") or "") != head:
        return False, "packet_head_mismatch"
    if str(payload.get("case_id") or "") != case_id:
        return False, "forged_case_membership"
    if str(payload.get("finding_id") or "") != finding_id:
        return False, "packet_finding_mismatch"
    if work_item_id and str(payload.get("work_item_id") or "") != work_item_id:
        return False, "packet_work_item_mismatch"
    if payload.get("EXECUTION_TERMINAL") != "CONFIRMED":
        return False, "execution_not_terminal"
    return True, "ok"


def validate_harvest_bundle(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    case_id: str,
    finding_id: str,
    work_item_id: str,
    production: bool,
) -> tuple[bool, str]:
    green = (
        repo_root
        / "opsx"
        / "runs"
        / "bug-cases"
        / case_id
        / "results"
        / f"GREEN-{finding_id}.md"
    )
    ok, reason = validate_green_binding(
        green,
        case_id=case_id,
        finding_id=finding_id,
        execution_id=execution_id,
        production=production,
    )
    if not ok:
        return False, reason
    packet_path = green.parent / f"BUG-TEAM-{finding_id}.json"
    packet = _load_json(packet_path)
    if packet is None:
        return False, "missing_bug_team_packet"
    ok, reason = validate_bug_team_packet(
        packet,
        case_id=case_id,
        finding_id=finding_id,
        execution_id=execution_id,
        head=head,
        work_item_id=work_item_id,
        production=production,
    )
    if not ok:
        return False, reason
    if production:
        allowed = AUTHORIZED_PACKET_PROVENANCE | {HERMETIC_PROVENANCE}
        ok, reason = validate_execution_terminal(
            repo_root,
            mission_id=mission_id,
            execution_id=execution_id,
            head=head,
            allowed_sources=AUTHORIZED_PACKET_PROVENANCE,
        )
        if not ok:
            return False, reason
    return True, "ok"
