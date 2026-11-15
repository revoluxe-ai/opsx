# -*- coding: utf-8 -*-
"""Hermetic Temporal verification E2E matrix A–H (in-process mock, no live server)."""

from __future__ import annotations

from pathlib import Path

import pytest

from verification_engine import (
    TERMINAL_BLOCKED_CHECK,
    TERMINAL_FAIL,
    TERMINAL_SUCCESS,
    TERMINAL_UNCERTAIN,
)
from verification_loop import VerificationExecutionLoop
from verification_mock import VerificationMockHarness, clear_mock_harnesses

# A–H ↔ verification-scenario1..8 (stable mock harness names).
MATRIX: tuple[tuple[str, str, str], ...] = (
    ("A", "verification-scenario1", TERMINAL_SUCCESS),
    ("B", "verification-scenario2", TERMINAL_FAIL),
    ("C", "verification-scenario3", TERMINAL_BLOCKED_CHECK),
    ("D", "verification-scenario4", "BLOCKED_SNAPSHOT_DRIFT"),
    ("E", "verification-scenario5", "EVIDENCE_INVALID"),
    ("F", "verification-scenario6", TERMINAL_SUCCESS),
    ("G", "verification-scenario7", TERMINAL_SUCCESS),
    ("H", "verification-scenario8", TERMINAL_UNCERTAIN),
)


@pytest.mark.parametrize("label,scenario,expected", MATRIX)
def test_matrix_row(tmp_path: Path, label: str, scenario: str, expected: str) -> None:
    clear_mock_harnesses()
    harness = VerificationMockHarness.from_scenario(scenario, change="matrix-change", repo=tmp_path)
    outcome = VerificationExecutionLoop(
        change="matrix-change",
        run_next_check=harness.run_next_check,
        run_assigned_check=harness.run_assigned_check,
    ).run()
    assert outcome.status == expected, f"matrix {label} ({scenario})"
