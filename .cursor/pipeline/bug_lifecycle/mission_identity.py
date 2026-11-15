"""Bind and enforce mission execution identity across runtime_state transitions."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def findings_digest(findings: list[dict[str, str]]) -> str:
    normalized = sorted(
        [{"finding": r.get("finding", ""), "location": r.get("location", "")} for r in findings],
        key=lambda row: (row["finding"], row["location"]),
    )
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def bind_identity(state: dict[str, Any], request: dict[str, Any], findings: list[dict[str, str]]) -> None:
    state["identity"] = {
        "mission_id": str(request.get("mission") or ""),
        "execution_id": str(request.get("execution_id") or ""),
        "head": str(request.get("head") or ""),
        "change_id": str(request.get("change") or ""),
        "findings_digest": findings_digest(findings),
    }


def verify_identity(
    state: dict[str, Any],
    request: dict[str, Any],
    findings: list[dict[str, str]],
) -> tuple[bool, str]:
    bound = state.get("identity")
    if not isinstance(bound, dict):
        return True, "ok"
    mission = str(request.get("mission") or "")
    if bound.get("mission_id") != mission:
        return False, "identity_mission_mismatch"
    if bound.get("execution_id") != str(request.get("execution_id") or ""):
        return False, "identity_execution_mismatch"
    if bound.get("head") != str(request.get("head") or ""):
        return False, "identity_head_mismatch"
    if bound.get("change_id") != str(request.get("change") or ""):
        return False, "identity_change_mismatch"
    if findings and bound.get("findings_digest") != findings_digest(findings):
        return False, "identity_findings_mismatch"
    return True, "ok"
