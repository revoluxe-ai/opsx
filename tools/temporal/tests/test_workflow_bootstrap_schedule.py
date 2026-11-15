"""A2: workflow ensure_cursor_session schedule uses contract STC/HB."""

from __future__ import annotations

import ast
from pathlib import Path


def test_workflow_module_parses_after_review_repair_loop_import() -> None:
    """Regression: hunt handoff merge left loop_engine import outside imports_passed_through (SyntaxError)."""

    workflow_py = Path(__file__).resolve().parents[1] / "workflow.py"
    ast.parse(workflow_py.read_text(encoding="utf-8"), filename=str(workflow_py))


def test_workflow_ensure_cursor_session_schedule_tokens_a2() -> None:
    workflow_py = Path(__file__).resolve().parents[1] / "workflow.py"
    text = workflow_py.read_text(encoding="utf-8")
    assert "ensure_cursor_session" in text
    assert "bootstrap_start_to_close_timeout()" in text
    assert "bootstrap_heartbeat_timeout()" in text
    assert "timedelta(minutes=5)" not in text.split("ensure_cursor_session", 1)[1][:400]
