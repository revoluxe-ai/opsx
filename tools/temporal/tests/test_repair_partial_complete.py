from parsers import parse_opsx_bug_fix_result


def test_last_status_wins_when_orch_prints_active_then_clear():
    raw = """STATUS: ACTIVE
MISSION_STATUS: ACTIVE
BUG_BATCH_CLEAR: NO
RUNNING_WORKER: YES

Bug Team вернул READY_FOR_CLOSURE
STATUS: BUG_BATCH_CLEAR
MISSION_ID: ORCH-bug-fix-20260830T084500Z
OWNER_AGGREGATE: FIX_NOW empty · BUG_BATCH_CLEAR YES · RUNNING_WORKER none
"""
    parsed = parse_opsx_bug_fix_result(raw)
    assert parsed.completed is True
    assert parsed.bug_batch_clear is True
    assert parsed.uncertain is False


def test_partial_complete_is_not_uncertain():
    raw = """STATUS: PARTIAL_COMPLETE
MISSION_STATUS: PARTIAL_COMPLETE
BUG_BATCH_CLEAR: NO
RUNNING_WORKER: none
UNRESOLVED:
  none
"""
    parsed = parse_opsx_bug_fix_result(raw)
    assert parsed.completed is True
    assert parsed.bug_batch_clear is False
    assert parsed.uncertain is False
