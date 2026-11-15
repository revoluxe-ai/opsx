"""Operator PASS must mean product-clean only (pipelines.json contract)."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline_operator_status import (
    is_product_clean_outcome,
    pipeline_exit_success,
    resolve_operator_status,
)

SPEC = json.loads(
    (Path(__file__).resolve().parents[1] / "pipelines.json").read_text(encoding="utf-8")
)["pipelines"]["project-bug-hunt"]


def test_hunt_clean_is_pass_and_exit_success():
    assert resolve_operator_status("HUNT_CLEAN", SPEC) == "PASS"
    assert pipeline_exit_success("HUNT_CLEAN", SPEC) is True
    assert is_product_clean_outcome("HUNT_CLEAN", SPEC) is True


def test_no_repair_progress_is_stop_not_pass():
    assert resolve_operator_status("NO_REPAIR_PROGRESS", SPEC) == "STOP"
    assert pipeline_exit_success("NO_REPAIR_PROGRESS", SPEC) is False


def test_stalled_and_max_rounds_are_stop_not_pass():
    assert resolve_operator_status("STALLED", SPEC) == "STOP"
    assert resolve_operator_status("MAX_ROUNDS_REACHED", SPEC) == "STOP"
    assert pipeline_exit_success("STALLED", SPEC) is False


def test_blocked_evidence_gap_is_blocked_not_pass():
    assert resolve_operator_status("BLOCKED_EVIDENCE_GAP", SPEC) == "BLOCKED"
    assert pipeline_exit_success("BLOCKED_EVIDENCE_GAP", SPEC) is False


def test_partial_complete_and_fail_closed_are_stop():
    assert resolve_operator_status("PARTIAL_COMPLETE", SPEC) == "STOP"
    assert resolve_operator_status("FAIL_CLOSED", SPEC) == "STOP"
