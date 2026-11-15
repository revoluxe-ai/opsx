# -*- coding: utf-8 -*-
"""P0-B: classify_next_check technical failure path (no UnboundLocalError)."""

from __future__ import annotations

import json
from pathlib import Path

from verification_engine import classify_next_check, ledger_path


def test_technical_failure_missing_ledger(tmp_path: Path) -> None:
    result = classify_next_check(
        repo=tmp_path,
        change="missing",
        raw_output="",
        completed=False,
        agent_exit_code=1,
    )
    assert result.status == "NEXT_CHECK_TECHNICAL_FAILURE"
    assert result.completed is False
    assert result.ledger_path is None


def test_technical_failure_with_ledger_present(tmp_path: Path) -> None:
    change = "has-ledger"
    ledger_path(tmp_path, change).parent.mkdir(parents=True, exist_ok=True)
    ledger_path(tmp_path, change).write_text(
        json.dumps(
            {
                "schema_version": "bounded-verify-ledger-v1",
                "ledger_id": "l",
                "plan_id": "p",
                "plan_fingerprint": "a" * 64,
                "ledger_revision": 1,
                "ledger_status": "ACTIVE",
                "entries": [{"check_id": "pyright-strict", "order": 1, "state": "CURRENT"}],
                "current_assignment": {"check_id": "pyright-strict", "assignment_epoch": 1},
            }
        ),
        encoding="utf-8",
    )
    result = classify_next_check(
        repo=tmp_path,
        change=change,
        raw_output="",
        completed=False,
        agent_exit_code=2,
    )
    assert result.status == "NEXT_CHECK_TECHNICAL_FAILURE"
    assert result.plan_id == "p"
    assert result.ledger_path is not None


def test_invalid_ledger_not_success(tmp_path: Path) -> None:
    change = "bad-json"
    path = ledger_path(tmp_path, change)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    result = classify_next_check(
        repo=tmp_path,
        change=change,
        raw_output="STATUS: ALL_REQUIRED_CHECKS_PASS",
        completed=True,
    )
    assert result.status == "BLOCKED"
    assert result.block_reason == "ledger_invalid"
