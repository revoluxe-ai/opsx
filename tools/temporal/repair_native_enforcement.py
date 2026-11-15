"""Temporal repair-hop gate: native orch S0–S11 only (no legacy adapter envelopes)."""

from __future__ import annotations

import re

from models import RepairRunResult

_LEGACY_FORBIDDEN_MARKERS = (
    re.compile(r"^\s*STATUS:\s*REPAIR_DISPATCH\s*$", re.I | re.M),
    re.compile(r"BATCH_CLEAR_AUTHORITY:\s*LEDGER_ONLY", re.I),
    re.compile(r"^\s*MISSION_STATUS:\s*ACTIVE\s*$", re.I | re.M),
)

_NATIVE_ALLOWED_MARKERS = (
    re.compile(r"native_mission\s*=\s*YES", re.I),
    re.compile(r"NATIVE_MISSION(?:_DRIVER)?:\s*YES", re.I),
    re.compile(r"^\s*STATUS:\s*STAGE_ADVANCED\s*$", re.I | re.M),
    re.compile(r"^\s*STATUS:\s*DISPATCH_REQUIRED\s*$", re.I | re.M),
    re.compile(r"TERMINAL_GATE:\s*PASS", re.I),
    re.compile(r"^\s*STATUS:\s*BUG_BATCH_CLEAR\s*$", re.I | re.M),
    re.compile(r"^\s*STATUS:\s*(?:FAIL_CLOSED|PARTIAL_COMPLETE|BLOCKED_[A-Z_]+|PAUSE)\s*$", re.I | re.M),
)


class NativeRepairEnforcementError(Exception):
    """Repair hop violated native orch driver contract."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def enforce_native_orch_repair(raw_output: str, parsed: RepairRunResult) -> RepairRunResult:
    """
    Reject legacy ``/opsx-orch-bug-fix`` adapter output on Temporal bugbot/hunt lanes.

    Chat/SDK repair must show native driver evidence, not ``REPAIR_DISPATCH``.
    """

    text = raw_output or ""
    for pattern in _LEGACY_FORBIDDEN_MARKERS:
        if pattern.search(text):
            if not any(marker.search(text) for marker in _NATIVE_ALLOWED_MARKERS):
                blocked = RepairRunResult(
                    completed=True,
                    bug_batch_clear=False,
                    unresolved=["LEGACY_ORCH_ADAPTER_FORBIDDEN"],
                    running_worker=None,
                    raw_output=text,
                    uncertain=False,
                    repaired_this_batch=False,
                    terminal_status="REPAIR_BLOCKED_LEGACY_DISPATCH",
                )
                return blocked
    if "PRODUCT_FINDINGS" in text and "ROUTED_ITEMS" not in text and "WORK_ITEMS" not in text:
        if not re.search(r"native_mission\s*=\s*YES", text, re.I):
            return RepairRunResult(
                completed=True,
                bug_batch_clear=False,
                unresolved=["LEGACY_PRODUCT_FINDINGS_DISPATCH"],
                running_worker=None,
                raw_output=text,
                uncertain=False,
                repaired_this_batch=False,
                terminal_status="REPAIR_BLOCKED_LEGACY_DISPATCH",
            )
    return parsed
