"""S3 disposition routing (repair vs defer/FP/security/process/evolution)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DISPOSITION_RE = re.compile(r"^DISPOSITION:\s*(\S+)", re.I | re.M)
ROUTING_SPLIT_RE = re.compile(r"^ROUTING_SPLIT_COMPLETE:\s*YES\b", re.I | re.M)
MUST_FIX_MEMBERS_RE = re.compile(
    r"^MUST_FIX(?:_CURRENT_CHANGE)?_MEMBER_IDS:\s*\[([^\]]*)\]",
    re.I | re.M,
)
REQUIRES_SPLIT_RE = re.compile(r"^REQUIRES_ROUTING_SPLIT:\s*YES\b", re.I | re.M)

ROUTE_MUST_FIX_REPAIR = "MUST_FIX_REPAIR"
ROUTE_NON_REPAIR = "NON_REPAIR"
ROUTE_SECURITY_PROCESS = "SECURITY_PROCESS_ROUTE"
ROUTE_TECH_DEBT = "TECH_DEBT_EVOLUTION"
ROUTE_GLOBAL_BLOCKER = "GLOBAL_RELEASE_BLOCKER"
ROUTE_MIXED_SPLIT = "MIXED_SPLIT"
ROUTE_BLOCKED_ADJUDICATION = "BLOCKED_ADJUDICATION"
ROUTE_BLOCKED_UNRESOLVED = "BLOCKED_UNRESOLVED"


def read_disposition(repo_root: Path, case_id: str, finding_id: str) -> str:
    path = repo_root / "opsx/runs/bug-cases" / case_id / "findings" / f"{finding_id}.md"
    if not path.is_file():
        return "UNRESOLVED"
    match = DISPOSITION_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        return "UNRESOLVED"
    return match.group(1).strip().upper()


def _read_finding_text(repo_root: Path, case_id: str, finding_id: str) -> str:
    path = repo_root / "opsx/runs/bug-cases" / case_id / "findings" / f"{finding_id}.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _parse_member_ids(text: str) -> list[str]:
    match = MUST_FIX_MEMBERS_RE.search(text)
    if not match:
        return []
    inner = match.group(1).strip()
    if not inner:
        return []
    return [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]


def classify_route(disposition: str) -> str:
    """Классификация disposition → маршрут S3 (матрица GAME opsx-orch-bug-fix)."""

    disp = (disposition or "").strip().upper()
    if disp in {"UNRESOLVED", ""}:
        return ROUTE_BLOCKED_UNRESOLVED
    if disp in {"NEEDS_ADJUDICATION", "AUTHORITY_CONFLICT"}:
        return ROUTE_BLOCKED_ADJUDICATION
    if disp == "MIXED_DISPOSITION":
        return ROUTE_MIXED_SPLIT
    if disp == "TECH_DEBT_ONLY" or disp.startswith("TECH_DEBT"):
        return ROUTE_TECH_DEBT
    if disp == "GLOBAL_RELEASE_BLOCKER" or disp.startswith("GLOBAL_RELEASE"):
        return ROUTE_GLOBAL_BLOCKER
    if disp.startswith("FALSE_POSITIVE") or disp.startswith("DEFER"):
        return ROUTE_NON_REPAIR
    if "SECURITY" in disp or disp.startswith("PROCESS") or disp == "SECURITY_TRIAGE_REQUIRED":
        return ROUTE_SECURITY_PROCESS
    if disp in {"MUST_FIX_CURRENT_CHANGE", "MUST_FIX"} or "MUST_FIX" in disp:
        return ROUTE_MUST_FIX_REPAIR
    if "STRUCTURAL_DEBT_TAIL" in disp:
        return ROUTE_MUST_FIX_REPAIR
    return ROUTE_MUST_FIX_REPAIR


def _enrich_mixed_route(
    repo_root: Path,
    item: dict[str, Any],
    route: str,
) -> tuple[str, dict[str, Any]]:
    if route != ROUTE_MIXED_SPLIT:
        return route, {}
    text = _read_finding_text(repo_root, item["case_id"], item["finding_id"])
    if REQUIRES_SPLIT_RE.search(text) and not ROUTING_SPLIT_RE.search(text):
        return "BLOCKED_MIXED_UNSPLIT", {}
    member_ids = _parse_member_ids(text)
    extras: dict[str, Any] = {"routing_split_complete": bool(ROUTING_SPLIT_RE.search(text))}
    if member_ids:
        extras["must_fix_member_ids"] = member_ids
        extras["s4_repair_required"] = True
        return ROUTE_MIXED_SPLIT, extras
    if ROUTING_SPLIT_RE.search(text):
        extras["s4_repair_required"] = True
        return ROUTE_MIXED_SPLIT, extras
    return "BLOCKED_MIXED_UNSPLIT", {}


def route_work_items(
    repo_root: Path,
    work_items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    routed: list[dict[str, Any]] = []
    blocked: list[str] = []
    for item in work_items:
        disp = read_disposition(repo_root, item["case_id"], item["finding_id"])
        route = classify_route(disp)
        if route == ROUTE_BLOCKED_UNRESOLVED:
            blocked.append(item["finding_id"])
            continue
        if route == ROUTE_BLOCKED_ADJUDICATION:
            blocked.append(f"{item['finding_id']}:adjudication_required")
            continue
        mixed_route, extras = _enrich_mixed_route(repo_root, item, route)
        if mixed_route == "BLOCKED_MIXED_UNSPLIT":
            blocked.append(f"{item['finding_id']}:mixed_routing_split_required")
            continue
        row: dict[str, Any] = {**item, "disposition": disp, "route": mixed_route, **extras}
        if mixed_route == ROUTE_MIXED_SPLIT and extras.get("must_fix_member_ids"):
            row["member_ids"] = extras["must_fix_member_ids"]
        routed.append(row)
    return routed, blocked


def split_routes_summary(routed: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in routed:
        key = str(row.get("route") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts
