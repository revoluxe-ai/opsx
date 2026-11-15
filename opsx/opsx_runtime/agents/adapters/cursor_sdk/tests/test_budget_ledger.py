"""Unit tests: TaskBudgetLedger unknown usage → estimate, block on cap."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agents.budget.guard import BudgetExceeded, BudgetGuard
from agents.budget.ledger import load_task_ledger, reconcile_usage, save_task_ledger
from agents.budget.profiles import AgentBudgetProfile
from agents.budget.session import begin_hop_budget
from agents.adapters.cursor_sdk import cursor_sdk_stream as stream_mod


def _tiny_profile() -> AgentBudgetProfile:
    return AgentBudgetProfile(
        max_observed_input_per_hop=10_000,
        max_observed_output_per_hop=1_000,
        max_observed_input_per_task=50_000,
        max_observed_output_per_task=5_000,
        max_tool_invocations_per_hop=2,
    )


def test_unknown_usage_records_estimate_not_auto_block(tmp_path: Path) -> None:
    profile = _tiny_profile()
    ledger = load_task_ledger(tmp_path, task_id="t1", profile=profile)
    reconcile_usage(ledger, hop="001", usage=None, estimate=100)
    assert ledger.accumulated_estimated_input == 100
    assert ledger.accumulated_observed_input == 0


def test_pre_hop_blocks_when_estimate_exceeds_task_cap(tmp_path: Path) -> None:
    state = {"output_dir": str(tmp_path), "mission_id": "m1"}
    profile = _tiny_profile()
    ledger = load_task_ledger(tmp_path, task_id="m1", profile=profile)
    ledger.accumulated_observed_input = 49_900
    save_task_ledger(tmp_path, ledger)
    with pytest.raises(BudgetExceeded):
        _ = begin_hop_budget(
            state,
            role="IMPLEMENTATION_EXECUTOR",
            hop="002-hop",
            prompt="x" * 8000,
        )


def test_intra_run_usage_raises_budget_exceeded() -> None:
    profile = _tiny_profile()
    ledger = load_task_ledger(Path("."), task_id="t", profile=profile)
    ledger.accumulated_observed_input = 49_000
    guard = BudgetGuard(profile=profile, ledger=ledger)
    sink = stream_mod.CursorRunStreamSink(
        label="t",
        ndjson_path=None,
        emit_stderr=False,
        intra_run_budget_guard=guard,
    )
    with pytest.raises(BudgetExceeded):
        sink._apply_intra_run_budget(
            {
                "kind": "usage",
                "usage": {
                    "input_tokens": 5_000,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                },
            },
        )


def test_ledger_persists_across_begin_hop(tmp_path: Path) -> None:
    state = {"output_dir": str(tmp_path), "mission_id": "persist-mission"}
    begin_hop_budget(state, role="CLASSIFIER", hop="001", prompt="hello world")
    path = tmp_path / "task_budget_ledger.json"
    assert path.is_file()
    guard2, _, _ = begin_hop_budget(state, role="CLASSIFIER", hop="002", prompt="again")
    assert guard2 is not None
    assert guard2.ledger.task_id == "persist-mission"


def test_load_task_ledger_refreshes_expired_deadline(tmp_path: Path) -> None:
    """Stale ``task_budget_ledger.json`` не должен сразу бить ``BudgetExceeded`` на новом прогоне."""
    profile = _tiny_profile()
    stale = {
        "task_id": "verify-static-visual-F-0003",
        "profile_id": "standard_impl",
        "max_observed_input_per_task": 50_000,
        "max_observed_output_per_task": 5_000,
        "accumulated_observed_input": 0,
        "accumulated_estimated_input": 0,
        "accumulated_observed_output": 0,
        "accumulated_estimated_output": 0,
        "model_turns": 0,
        "tool_calls": 0,
        "deadline_unix": 1,
        "entries": [],
    }
    path = tmp_path / "task_budget_ledger.json"
    path.write_text(json.dumps(stale), encoding="utf-8")
    now = int(time.time())
    ledger = load_task_ledger(
        tmp_path,
        task_id="verify-static-visual-F-0003",
        profile=profile,
    )
    assert ledger.deadline_unix is not None
    assert ledger.deadline_unix > now


def test_account_tool_invocation_dedupes_running_and_completed_by_call_id() -> None:
    counted: set[str] = set()
    cid = "call-abc"
    assert stream_mod.account_tool_invocation_in_stream(
        {"kind": "tool_call", "name": "read", "call_id": cid, "status": "running"},
        counted,
    )
    assert not stream_mod.account_tool_invocation_in_stream(
        {"kind": "tool_call", "name": "read", "call_id": cid, "status": "completed"},
        counted,
    )
    assert not stream_mod.account_tool_invocation_in_stream(
        {
            "kind": "interaction",
            "update_type": "tool-call-started",
            "call_id": cid,
        },
        counted,
    )
    assert len(counted) == 1


def test_sink_tool_guard_one_debit_per_call_id() -> None:
    profile = _tiny_profile()
    ledger = load_task_ledger(Path("."), task_id="dedupe", profile=profile)
    guard = BudgetGuard(profile=profile, ledger=ledger)
    sink = stream_mod.CursorRunStreamSink(
        label="dedupe",
        ndjson_path=None,
        emit_stderr=False,
        intra_run_budget_guard=guard,
    )
    cid_a, cid_b = "a-1", "b-2"
    for status in ("running", "completed"):
        sink._enforce_tool_guard(
            {"kind": "tool_call", "name": "grep", "call_id": cid_a, "status": status},
        )
    sink._enforce_tool_guard(
        {
            "kind": "interaction",
            "update_type": "tool-call-started",
            "call_id": cid_b,
        },
    )
    sink._enforce_tool_guard(
        {"kind": "tool_call", "name": "read", "call_id": cid_b, "status": "completed"},
    )
    assert guard.hop_tool_calls == 2
    with pytest.raises(BudgetExceeded, match="max tool invocations"):
        sink._enforce_tool_guard(
            {"kind": "tool_call", "name": "edit", "call_id": "c-3", "status": "running"},
        )
