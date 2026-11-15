"""Handoff при BLOCKED IMPLEMENTATION_EXECUTOR без shell."""

from __future__ import annotations

from agents.verify.repair.executor_blocked_handoff import (
    build_executor_blocked_product_handoff_owner,
    maybe_executor_blocked_product_handoff_patch,
    maybe_executor_blocked_terminal_pause_patch,
)


def test_executor_blocked_handoff_when_integrity_complete() -> None:
    node = (
        "tests/unit/channels/max/invest_yalta/"
        "test_funnel_self_walk.py::test_max_funnel_self_walk_green"
    )
    state = {
        "change_id": "visual",
        "quality_gate_diagnostics": {
            "pytest": [f"pytest: {node} - AssertionError: invite"],
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
        },
        "dispatcher_current_item": {
            "observation": f"pytest: {node} - AssertionError: invite",
            "finding_id": "F-0001",
        },
        "verify_finding_fix": {
            "status": "BLOCKED",
            "summary": "pytest could not be executed in this hop (no shell/runtime).",
            "notes": "execution tools unavailable",
            "files_changed": [],
        },
        "verify_static_repair_state_snapshot": {
            "mission_id": "verify-static-visual-F-0001",
        },
    }
    patch = maybe_executor_blocked_product_handoff_patch(state)
    assert patch
    assert "REPAIR_PRODUCT_HANDOFF_BLOCKED" in patch["owner_attention"]
    assert node in patch["owner_attention"]
    blocked = patch["quality_gate_diagnostics"]["repair_execution_blocked"]
    assert blocked[0]["named_test"] == node
    assert blocked[0]["reason"] == "no_shell_no_pytest_rerun"


def test_executor_blocked_ignored_when_integrity_stays_partial() -> None:
    """Без node id в handoff integrity остаётся PARTIAL — product handoff не подменяет gate."""
    state = {
        "quality_gate_diagnostics": {
            "pytest": [],
            "input_integrity_status": "PARTIAL",
            "input_integrity_issues": [
                "pytest exit non-zero but no FAILED/ERROR node ids parsed from log",
            ],
        },
        "verify_finding_fix": {
            "status": "BLOCKED",
            "summary": "no shell/runtime",
        },
    }
    assert maybe_executor_blocked_product_handoff_patch(state) == {}


def test_terminal_pause_uses_product_handoff_not_incomplete_diagnostics() -> None:
    node = "tests/unit/foo.py::test_bar"
    state = {
        "quality_gate_diagnostics": {
            "pytest": [f"pytest: {node} - AssertionError"],
            "input_integrity_status": "COMPLETE",
            "input_integrity_issues": [],
        },
        "verify_finding_fix": {
            "status": "BLOCKED",
            "summary": "could not be executed (no shell)",
        },
        "dispatcher_current_item": {"observation": f"pytest: {node} - AssertionError"},
    }
    pause = maybe_executor_blocked_terminal_pause_patch(state)
    assert pause is not None
    assert pause["verify_loop_status"] == "PAUSE"
    assert "REPAIR_PRODUCT_HANDOFF_BLOCKED" in pause["owner_attention"]
    assert "incomplete or invalid diagnostics" not in pause["owner_attention"]


def test_owner_message_shape() -> None:
    text = build_executor_blocked_product_handoff_owner(
        hop="001-implementation-executor-e68d58",
        named_test="tests/unit/x.py::test_y",
        reason="no_shell_no_pytest_rerun",
    )
    assert "001-implementation-executor-e68d58" in text
    assert "named_test=tests/unit/x.py::test_y" in text
    assert "quality_gate_verdict remains FAIL" in text
