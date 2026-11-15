"""SDK outcome unknown → implementation PAUSED (не uncaught exception)."""

from __future__ import annotations

import pytest

from agents.adapters.abc_adapter import ActiveCursorAgentRunError
from workflows.implementation.routing_contract import RUN_STATUS_PAUSED
from workflows.implementation.adapter_hop_pause import (
    SDK_ACTIVE_RUN_CONFLICT,
    SDK_BRIDGE_TIMEOUT,
    patch_implementation_adapter_outcome_unknown,
)


def test_sdk_active_run_conflict_pause_patch() -> None:
    exc = ActiveCursorAgentRunError("agent-414b3a36")
    patch = patch_implementation_adapter_outcome_unknown(
        stage="APPLY_RED",
        slice_id="SLICE-004",
        agent_id=exc.agent_id,
        run_id="run-5f108d01",
        exc=exc,
    )
    assert patch["implementation_status"] == RUN_STATUS_PAUSED
    assert patch["implementation_recovery_code"] == SDK_ACTIVE_RUN_CONFLICT
    assert patch["implementation_slice_id"] == "SLICE-004"
    assert "agent-414b3a36" in patch["error"]


def test_sdk_bridge_timeout_code() -> None:
    try:
        from agents.adapters.provider_errors import APITimeoutError
    except ImportError:
        pytest.skip("provider_errors not importable")
    exc = APITimeoutError("Bridge request timed out")
    patch = patch_implementation_adapter_outcome_unknown(
        stage="APPLY_RED",
        slice_id="SLICE-004",
        agent_id="agent-x",
        run_id="run-y",
        exc=exc,
    )
    assert patch["implementation_recovery_code"] == SDK_BRIDGE_TIMEOUT


def test_wrapped_bridge_timeout_uses_current_run_not_previous_hop() -> None:
    """Regression: state.run_id предыдущего hop не должен попасть в pause patch."""
    try:
        from agents.adapters.provider_errors import APITimeoutError
    except ImportError:
        pytest.skip("provider_errors not importable")
    cause = APITimeoutError("Bridge request timed out")
    exc = ActiveCursorAgentRunError(
        "agent-current",
        "run-current",
        code=SDK_BRIDGE_TIMEOUT,
    )
    exc.__cause__ = cause
    # Graph передаёт exc.run_id; stale state["run_id"] = run-previous не используется.
    patch = patch_implementation_adapter_outcome_unknown(
        stage="APPLY_RED",
        slice_id="SLICE-004",
        agent_id=exc.agent_id,
        run_id=exc.run_id,
        exc=exc,
    )
    assert patch["implementation_recovery_code"] == SDK_BRIDGE_TIMEOUT
    assert patch["implementation_recovery_code"] != SDK_ACTIVE_RUN_CONFLICT
    gate = patch["implementation_machine_gate_errors"][0]
    assert gate["run_id"] == "run-current"
    assert gate["run_id"] != "run-previous"
    assert gate["agent_id"] == "agent-current"
    assert patch["run_id"] == "run-current"
    assert patch["agent_id"] == "agent-current"


def test_dispatch_gate_maps_ledger_vs_index_block() -> None:
    """Pre-agent dispatch PAUSE: ledger vs index/live run — разные recovery codes."""
    from workflows.implementation.code_pipeline.graph import dispatch_gate_recovery_code

    assert (
        dispatch_gate_recovery_code(
            "dispatch_ledger_active_hop stage=APPLY_IMPLEMENT run_ids=run-x",
        )
        == "DISPATCH_LEDGER_ACTIVE_HOP"
    )
    assert (
        dispatch_gate_recovery_code(
            "sdk_index_get_run_block run_id=run-y disposition=running_live",
        )
        == SDK_ACTIVE_RUN_CONFLICT
    )
    assert (
        dispatch_gate_recovery_code("sdk_index_get_run_unavailable no_api_key run_id=run-z")
        == SDK_BRIDGE_TIMEOUT
    )


def test_unknown_run_id_overwrites_channel_instead_of_omitting_it() -> None:
    """Пустой текущий run_id должен затереть канал, а не оставить id прошлого hop."""
    patch = patch_implementation_adapter_outcome_unknown(
        stage="APPLY_IMPLEMENT",
        slice_id="SLICE-004",
        agent_id="agent-current",
        run_id=None,
        exc=ActiveCursorAgentRunError("agent-current"),
    )
    assert "run_id" in patch
    assert patch["run_id"] == ""
    assert patch["run_id"] != "run-previous"
    assert patch["agent_id"] == "agent-current"
