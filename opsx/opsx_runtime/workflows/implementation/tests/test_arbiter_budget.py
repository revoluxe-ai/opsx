"""Бюджет вызовов implementation arbiter (3 на run)."""

from __future__ import annotations

import pytest

from workflows.implementation.routing import (
    ARBITER_MAX_CALLS,
    ImplementationLaneTerminalError,
    OUTCOME_ENTER_ARBITER,
    route_after_apply_design_lane,
    route_after_one_slice,
)


def test_pending_escalation_routes_to_arbiter() -> None:
    state = {
        "implementation_arbiter_pending": True,
        "implementation_arbiter_calls": 0,
        "implementation_status": "RUNNING",
    }
    assert route_after_one_slice(state) == OUTCOME_ENTER_ARBITER


def test_fourth_escalation_raises() -> None:
    state = {
        "implementation_arbiter_pending": True,
        "implementation_arbiter_calls": ARBITER_MAX_CALLS,
        "implementation_status": "RUNNING",
    }
    with pytest.raises(ImplementationLaneTerminalError, match="ARBITER_BUDGET_EXHAUSTED"):
        route_after_apply_design_lane(state)
