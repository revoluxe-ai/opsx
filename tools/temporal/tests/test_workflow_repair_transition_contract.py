"""Evidence-bound workflow transition contract (no Temporal server required)."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from models import classify_completed_repair
from parsers import parse_opsx_bug_fix_result
from workflow_repair_transition import (
    repair_activity_allows_commit_materialize,
    repair_activity_terminal_outcome,
)

_NON_SUCCESS_REPAIR_RAW = {
    "BLOCKED_TEST_ENV": (
        "STATUS: BLOCKED_TEST_ENV\nREPAIRED_THIS_BATCH: YES\nBUG_BATCH_CLEAR: NO\n"
    ),
    "FAIL_CLOSED": (
        "STATUS: FAIL_CLOSED\nREPAIRED_THIS_BATCH: YES\nBUG_BATCH_CLEAR: NO\n"
    ),
    "PARTIAL_COMPLETE": (
        "STATUS: PARTIAL_COMPLETE\nREPAIRED_THIS_BATCH: YES\nBUG_BATCH_CLEAR: NO\n"
    ),
    "BLOCKED_EVIDENCE_GAP": (
        "STATUS: BLOCKED_EVIDENCE_GAP\nREPAIRED_THIS_BATCH: NO\nBUG_BATCH_CLEAR: NO\n"
    ),
    "BLOCKED_EVIDENCE_IDENTITY": (
        "STATUS: BLOCKED_EVIDENCE_IDENTITY\nREPAIRED_THIS_BATCH: YES\nBUG_BATCH_CLEAR: YES\n"
    ),
    "BLOCKED_WRITE_OWNERSHIP": (
        "STATUS: BLOCKED_WRITE_OWNERSHIP\nREPAIRED_THIS_BATCH: YES\nBUG_BATCH_CLEAR: YES\n"
    ),
}

_SUCCESS_REPAIR_RAW = (
    "STATUS: BUG_BATCH_CLEAR\nBUG_BATCH_CLEAR: YES\nREPAIRED_THIS_BATCH: YES\n"
)


@pytest.mark.parametrize(
    "token",
    [
        "BLOCKED_TEST_ENV",
        "FAIL_CLOSED",
        "PARTIAL_COMPLETE",
        "BLOCKED_EVIDENCE_IDENTITY",
        "BLOCKED_WRITE_OWNERSHIP",
    ],
)
def test_non_success_yes_never_authorizes_materialize(token: str):
    parsed = parse_opsx_bug_fix_result(_NON_SUCCESS_REPAIR_RAW[token])
    payload = parsed.to_dict()
    assert repair_activity_allows_commit_materialize(payload) is False
    terminal = repair_activity_terminal_outcome(payload)
    assert terminal is not None
    assert terminal[0] == token
    assert terminal[0] != "NO_REPAIR_PROGRESS"


def test_blocked_evidence_gap_no_primary_outcome_is_exact_token():
    parsed = parse_opsx_bug_fix_result(_NON_SUCCESS_REPAIR_RAW["BLOCKED_EVIDENCE_GAP"])
    payload = parsed.to_dict()
    terminal = repair_activity_terminal_outcome(payload)
    assert terminal is not None
    assert terminal[0] == "BLOCKED_EVIDENCE_GAP"


def test_bug_batch_clear_yes_authorizes_materialize():
    parsed = parse_opsx_bug_fix_result(_SUCCESS_REPAIR_RAW)
    payload = parsed.to_dict()
    assert repair_activity_allows_commit_materialize(payload) is True
    assert repair_activity_terminal_outcome(payload) is None


def test_classify_completed_repair_has_no_parsers_import():
    """Workflow sandbox safety: repair gate must not import parsers at runtime."""
    source = inspect.getsource(classify_completed_repair)
    tree = ast.parse(source)
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "parsers"
    ]
    assert imports == []


def test_workflow_py_uses_classify_completed_repair_before_materialize():
    workflow_src = (Path(__file__).resolve().parents[1] / "workflow.py").read_text(
        encoding="utf-8"
    )
    materialize_idx = workflow_src.index("execute_commit_materialize")
    classify_idx = workflow_src.rindex("classify_completed_repair", 0, materialize_idx)
    assert classify_idx < materialize_idx
