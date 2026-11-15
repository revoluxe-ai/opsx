"""Константы и инварианты дедлайнов Temporal ↔ process_worker (bootstrap и project-bug-hunt).

Связь: workflow STC/HB, activity heartbeat loop, CursorExecutionRequest.timeout_seconds,
ProcessClient grace/cleanup. Не описывает SDK streaming (см. README).
"""

from __future__ import annotations

from datetime import timedelta

SESSION_BOOTSTRAP_STC_SECONDS = 1800
SESSION_BOOTSTRAP_HEARTBEAT_TIMEOUT_SECONDS = 120
SESSION_BOOTSTRAP_HEARTBEAT_INTERVAL_SECONDS = 30
SESSION_BOOTSTRAP_PROCESS_TIMEOUT_SECONDS = 1680
SESSION_BOOTSTRAP_TIMEOUT_GRACE_SECONDS = 20
SESSION_BOOTSTRAP_CLEANUP_BUDGET_SECONDS = 15


def process_deadline_seconds() -> int:
    """P1: process timeout + grace (hard communicate deadline in ProcessClient)."""

    return SESSION_BOOTSTRAP_PROCESS_TIMEOUT_SECONDS + SESSION_BOOTSTRAP_TIMEOUT_GRACE_SECONDS


def assert_bootstrap_deadline_invariant() -> None:
    """P1–P4: machine-checkable ordering of STC, HB, and process deadlines."""

    p1 = process_deadline_seconds()
    if p1 >= SESSION_BOOTSTRAP_STC_SECONDS:
        raise AssertionError(f"P2 failed: process_deadline {p1} >= STC {SESSION_BOOTSTRAP_STC_SECONDS}")
    if SESSION_BOOTSTRAP_HEARTBEAT_INTERVAL_SECONDS >= SESSION_BOOTSTRAP_HEARTBEAT_TIMEOUT_SECONDS:
        raise AssertionError("P3 failed: HB interval must be < HB timeout")
    if SESSION_BOOTSTRAP_HEARTBEAT_TIMEOUT_SECONDS >= SESSION_BOOTSTRAP_STC_SECONDS:
        raise AssertionError("P4 failed: HB timeout must be < STC")


def bootstrap_start_to_close_timeout() -> timedelta:
    return timedelta(seconds=SESSION_BOOTSTRAP_STC_SECONDS)


def bootstrap_heartbeat_timeout() -> timedelta:
    return timedelta(seconds=SESSION_BOOTSTRAP_HEARTBEAT_TIMEOUT_SECONDS)


PROJECT_BUG_HUNT_STC_SECONDS = 3600
PROJECT_BUG_HUNT_HEARTBEAT_TIMEOUT_SECONDS = 900
PROJECT_BUG_HUNT_HEARTBEAT_INTERVAL_SECONDS = 30
PROJECT_BUG_HUNT_PROCESS_TIMEOUT_SECONDS = 3480
PROJECT_BUG_HUNT_TIMEOUT_GRACE_SECONDS = SESSION_BOOTSTRAP_TIMEOUT_GRACE_SECONDS


def hunt_process_deadline_seconds() -> int:
    """Hard communicate deadline для hunt/orch hop (process + grace)."""

    return PROJECT_BUG_HUNT_PROCESS_TIMEOUT_SECONDS + PROJECT_BUG_HUNT_TIMEOUT_GRACE_SECONDS


def assert_hunt_deadline_invariant() -> None:
    """P1–P4 для execute_project_bug_hunt и execute_project_bug_hunt_orch."""

    p1 = hunt_process_deadline_seconds()
    if p1 >= PROJECT_BUG_HUNT_STC_SECONDS:
        raise AssertionError(
            f"P2 failed: hunt process_deadline {p1} >= STC {PROJECT_BUG_HUNT_STC_SECONDS}"
        )
    if PROJECT_BUG_HUNT_HEARTBEAT_INTERVAL_SECONDS >= PROJECT_BUG_HUNT_HEARTBEAT_TIMEOUT_SECONDS:
        raise AssertionError("P3 failed: hunt HB interval must be < HB timeout")
    if PROJECT_BUG_HUNT_HEARTBEAT_TIMEOUT_SECONDS >= PROJECT_BUG_HUNT_STC_SECONDS:
        raise AssertionError("P4 failed: hunt HB timeout must be < STC")


def hunt_start_to_close_timeout() -> timedelta:
    return timedelta(seconds=PROJECT_BUG_HUNT_STC_SECONDS)


def hunt_heartbeat_timeout() -> timedelta:
    return timedelta(seconds=PROJECT_BUG_HUNT_HEARTBEAT_TIMEOUT_SECONDS)
