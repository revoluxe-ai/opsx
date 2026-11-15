"""A1, A11: session bootstrap deadline contract invariants."""

from __future__ import annotations

from session_lifecycle_contract import (
    PROJECT_BUG_HUNT_HEARTBEAT_INTERVAL_SECONDS,
    PROJECT_BUG_HUNT_HEARTBEAT_TIMEOUT_SECONDS,
    PROJECT_BUG_HUNT_PROCESS_TIMEOUT_SECONDS,
    PROJECT_BUG_HUNT_STC_SECONDS,
    PROJECT_BUG_HUNT_TIMEOUT_GRACE_SECONDS,
    SESSION_BOOTSTRAP_CLEANUP_BUDGET_SECONDS,
    SESSION_BOOTSTRAP_HEARTBEAT_INTERVAL_SECONDS,
    SESSION_BOOTSTRAP_HEARTBEAT_TIMEOUT_SECONDS,
    SESSION_BOOTSTRAP_PROCESS_TIMEOUT_SECONDS,
    SESSION_BOOTSTRAP_STC_SECONDS,
    SESSION_BOOTSTRAP_TIMEOUT_GRACE_SECONDS,
    assert_bootstrap_deadline_invariant,
    assert_hunt_deadline_invariant,
    hunt_process_deadline_seconds,
    process_deadline_seconds,
)


def test_bootstrap_constants_a1() -> None:
    assert SESSION_BOOTSTRAP_STC_SECONDS == 1800
    assert SESSION_BOOTSTRAP_HEARTBEAT_TIMEOUT_SECONDS == 120
    assert SESSION_BOOTSTRAP_HEARTBEAT_INTERVAL_SECONDS == 30
    assert SESSION_BOOTSTRAP_PROCESS_TIMEOUT_SECONDS == 1680
    assert SESSION_BOOTSTRAP_TIMEOUT_GRACE_SECONDS == 20
    assert SESSION_BOOTSTRAP_CLEANUP_BUDGET_SECONDS == 15


def test_bootstrap_deadline_invariants_p1_p4() -> None:
    assert process_deadline_seconds() == 1680 + 20
    assert_bootstrap_deadline_invariant()


def test_hunt_constants_owner_one_hour_stc() -> None:
    assert PROJECT_BUG_HUNT_STC_SECONDS == 3600
    assert PROJECT_BUG_HUNT_HEARTBEAT_TIMEOUT_SECONDS == 900
    assert PROJECT_BUG_HUNT_HEARTBEAT_INTERVAL_SECONDS == 30
    assert PROJECT_BUG_HUNT_PROCESS_TIMEOUT_SECONDS == 3480
    assert PROJECT_BUG_HUNT_TIMEOUT_GRACE_SECONDS == SESSION_BOOTSTRAP_TIMEOUT_GRACE_SECONDS


def test_hunt_deadline_invariants_p1_p4() -> None:
    assert hunt_process_deadline_seconds() == 3480 + 20
    assert_hunt_deadline_invariant()
