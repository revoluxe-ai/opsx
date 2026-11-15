"""Hunt → orch repair handoff for Temporal project-bug-hunt lane.

Kernel injects explicit R1 findings and a fresh mission id so repair admission
does not fall through to R3 resume of a prior terminal mission on disk.
"""

from __future__ import annotations

import re
from typing import Any

from lanes import LaneSpec

_ADMISSION_SOURCE = "R1_TEMPORAL_HUNT_HANDOFF"
_FINDING_ID_RE = re.compile(r"^(?:PBH-|F-)?[\w-]+", re.I)


def _slug_change(change: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", change.strip().lower()).strip("-")
    return slug or "change"


def _finding_id_from_row(row: dict[str, str]) -> str:
    explicit = str(row.get("finding_id") or "").strip()
    if explicit:
        return explicit
    finding = str(row.get("finding") or "").strip()
    if not finding:
        return "UNKNOWN"
    token = finding.split(":", 1)[0].strip()
    match = _FINDING_ID_RE.match(token)
    return match.group(0) if match else token[:32]


def _finding_suffix(finding_ids: list[str]) -> str:
    parts: list[str] = []
    for fid in finding_ids:
        numbered = re.search(r"PBH-(\d+)", fid, re.I)
        if numbered:
            parts.append(numbered.group(1))
            continue
        generic = re.search(r"-(\d+)$", fid)
        if generic:
            parts.append(generic.group(1))
            continue
        parts.append(re.sub(r"[^a-zA-Z0-9]+", "", fid).lower()[:12] or "x")
    return "-".join(parts) if parts else "batch"


def mint_hunt_repair_mission_id(change: str, finding_ids: list[str]) -> str:
    """Mint a mission id for the current hunt batch (never reuse prior terminal missions)."""

    return f"ORCH-{_slug_change(change)}-pbh-{_finding_suffix(finding_ids)}"


def _format_findings_table(rows: list[dict[str, str]]) -> str:
    lines = ["| Severity | Location | Finding |", "| --- | --- | --- |"]
    for row in rows:
        severity = str(row.get("severity") or "Medium").strip() or "Medium"
        location = str(row.get("location") or "").strip()
        finding = str(row.get("finding") or row.get("finding_id") or "").strip()
        lines.append(f"| {severity} | {location} | {finding} |")
    return "\n".join(lines)


def build_handoff_payload(
    *,
    review: dict[str, Any],
    change: str,
    orchestration_execution_id: str,
    workflow_run_id: str,
    repair_number: int,
    review_fingerprint: str,
    head_sha: str,
) -> dict[str, Any]:
    """Machine payload passed from workflow → repair activity → loop engine."""

    repairable = list(review.get("repairable_findings") or [])
    if not repairable:
        actionable = list(review.get("actionable_findings") or review.get("findings") or [])
        repairable = actionable
    finding_ids = [_finding_id_from_row(row) for row in repairable]
    mission_id = mint_hunt_repair_mission_id(change, finding_ids)
    return {
        "admission_source": _ADMISSION_SOURCE,
        "change": change,
        "mission_id": mission_id,
        "finding_ids": finding_ids,
        "repairable_findings": repairable,
        "orchestration_execution_id": orchestration_execution_id,
        "workflow_run_id": workflow_run_id,
        "repair_number": repair_number,
        "review_fingerprint": review_fingerprint,
        "head_sha": head_sha,
    }


def build_repair_prompt(lane: LaneSpec, handoff: dict[str, Any]) -> str:
    """Full `/opsx-orch-bug-fix` prompt with inline HUNT_FINDINGS (R1)."""

    change = str(handoff.get("change") or "").strip()
    mission_id = str(handoff.get("mission_id") or "").strip()
    execution_id = str(handoff.get("orchestration_execution_id") or "").strip()
    head_sha = str(handoff.get("head_sha") or "").strip()
    fingerprint = str(handoff.get("review_fingerprint") or "").strip()
    rows = list(handoff.get("repairable_findings") or [])
    table = _format_findings_table(rows)
    base = lane.repair_prompt.strip()
    return (
        f"{base}\n\n"
        "TEMPORAL_HUNT_ORCH_HANDOFF:\n"
        f"- admission_source: {_ADMISSION_SOURCE}\n"
        "- SOURCE_PRECEDENCE: R1_INLINE\n"
        "- TERMINAL_PREVIOUS_MISSION_MUST_NOT_SHADOW: YES\n"
        f"- mission: {mission_id}\n"
        f"- execution_id: {execution_id}\n"
        f"- head: {head_sha}\n"
        f"- review_fingerprint: {fingerprint}\n"
        f"- change: {change}\n"
        "Rules:\n"
        "1. Admit exactly the HUNT_FINDINGS rows below for this mission id.\n"
        "2. Do NOT resume any prior terminal mission for this change.\n"
        "3. Bind native_mission request identity to execution_id, head, and review_fingerprint above.\n"
        "\n"
        "HUNT_FINDINGS:\n"
        f"{table}\n"
    )
