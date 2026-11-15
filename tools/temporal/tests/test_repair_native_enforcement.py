"""Temporal boundary: legacy REPAIR_DISPATCH rejected on native-required lanes."""

from __future__ import annotations

from repair_native_enforcement import enforce_native_orch_repair
from parsers import parse_opsx_bug_fix_result


def test_legacy_repair_dispatch_blocked():
    raw = """STATUS: REPAIR_DISPATCH
BUG_BATCH_CLEAR: NO
NEXT_COMMAND: /opsx-bug-team
PRODUCT_FINDINGS: one
"""
    parsed = parse_opsx_bug_fix_result(raw)
    enforced = enforce_native_orch_repair(raw, parsed)
    assert enforced.terminal_status == "REPAIR_BLOCKED_LEGACY_DISPATCH"
    assert enforced.bug_batch_clear is False


def test_native_dispatch_required_allowed():
    raw = """STATUS: DISPATCH_REQUIRED
native_mission=YES
BUG_BATCH_CLEAR: NO
NEXT_COMMAND: /opsx-bug-team
"""
    parsed = parse_opsx_bug_fix_result(raw)
    enforced = enforce_native_orch_repair(raw, parsed)
    assert enforced.terminal_status != "REPAIR_BLOCKED_LEGACY_DISPATCH"
