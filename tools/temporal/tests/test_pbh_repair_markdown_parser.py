"""Repair orch markdown machine tokens (`**STATUS:**`, etc.) for PBH materialize gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from parsers import parse_repair_result
from repair_native_enforcement import enforce_native_orch_repair
from workflow_repair_transition import repair_activity_allows_commit_materialize

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "project_bug_hunt"
    / "markdown_orch_bug_batch_clear_incident.txt"
)


def _incident_markdown_orch() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def test_markdown_orch_tokens_classify_bug_batch_clear():
    """Incident 01a17f1d: bold terminal fields must not yield uncertain repair."""
    raw = _incident_markdown_orch()
    parsed = parse_repair_result(raw)
    assert parsed.completed is True
    assert parsed.uncertain is False
    assert parsed.repaired_this_batch is True
    assert parsed.bug_batch_clear is True
    assert parsed.terminal_status == "BUG_BATCH_CLEAR"


def test_markdown_orch_red_plain_regex_misses_bold_keys():
    """RED repro: legacy line regex never sees **STATUS:** keys."""
    import re

    raw = _incident_markdown_orch()
    legacy = re.compile(r"^STATUS:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
    assert legacy.findall(raw) == []


def test_markdown_orch_survives_native_enforcement_and_materialize_gate():
    raw = _incident_markdown_orch()
    parsed = parse_repair_result(raw)
    enforced = enforce_native_orch_repair(raw, parsed)
    assert enforced.terminal_status == "BUG_BATCH_CLEAR"
    assert enforced.uncertain is False
    payload = enforced.to_dict()
    assert repair_activity_allows_commit_materialize(payload) is True


def test_markdown_status_value_wrapped_entire_line():
    raw = """**STATUS: BUG_BATCH_CLEAR**
**BUG_BATCH_CLEAR:** YES
**REPAIRED_THIS_BATCH:** YES
"""
    parsed = parse_repair_result(raw)
    assert parsed.completed is True
    assert parsed.terminal_status == "BUG_BATCH_CLEAR"
    assert parsed.repaired_this_batch is True


@pytest.mark.parametrize(
    "raw",
    [
        "**STATUS:** PARTIAL_COMPLETE\n**REPAIRED_THIS_BATCH:** NO\nBUG_BATCH_CLEAR: NO\n",
        "**STATUS:** BLOCKED_EVIDENCE_GAP\n**REPAIRED_THIS_BATCH:** NO\nBUG_BATCH_CLEAR: NO\n",
        "**STATUS:** FAIL_CLOSED\n**REPAIRED_THIS_BATCH:** YES\nBUG_BATCH_CLEAR: NO\n",
    ],
)
def test_markdown_non_success_terminals_do_not_authorize_materialize(raw: str):
    parsed = parse_repair_result(raw)
    assert parsed.completed is True
    assert parsed.uncertain is False
    assert repair_activity_allows_commit_materialize(parsed.to_dict()) is False


def test_plain_text_repair_tokens_unchanged():
    raw = (
        "STATUS: BUG_BATCH_CLEAR\n"
        "BUG_BATCH_CLEAR: YES\n"
        "REPAIRED_THIS_BATCH: YES\n"
    )
    parsed = parse_repair_result(raw)
    assert parsed.completed is True
    assert parsed.repaired_this_batch is True
    assert repair_activity_allows_commit_materialize(parsed.to_dict()) is True
