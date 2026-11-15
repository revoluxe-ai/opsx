"""Do not present prior IMPL_OK as the blocked scenario verification outcome."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from workflows.checkpoint_resume import (
    _maybe_continue_incomplete_verify_pipeline,
    format_checkpoint_snapshot_summary,
    plan_stale_end_resume,
)


def test_checkpoint_shows_separate_impl_and_scenario_verdicts():
    snapshot = SimpleNamespace(
        config={"configurable": {"checkpoint_id": "check-1"}},
        next=(),
        tasks=[],
        values={
            "verdict": "IMPL_OK",
            "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_UNKNOWN",
            "verify_stop_reason": "Owner Attention",
        },
    )
    text = format_checkpoint_snapshot_summary(snapshot)
    assert "impl_or_quality_verdict=IMPL_OK" in text
    assert "scenario_coverage_verdict=SCENARIO_TEST_COVERAGE_UNKNOWN" in text
    assert "verify_stop_reason=Owner Attention" in text


def test_checkpoint_without_scenario_audit_shows_unbound_not_pass():
    text = format_checkpoint_snapshot_summary(SimpleNamespace(
        next=(), tasks=[], values={"verdict": "IMPL_OK"},
    ))
    assert "impl_or_quality_verdict=IMPL_OK" in text
    assert "scenario_coverage_verdict=-" in text


@pytest.mark.asyncio
async def test_terminal_owner_attention_is_not_stale_end():
    snapshot = SimpleNamespace(
        next=(),
        values={
            "verdict": "IMPL_OK",
            "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_UNKNOWN",
            "verify_stop_reason": "Owner Attention",
        },
    )
    # No graph/checkpointer access should be attempted for a terminal pause.
    assert await plan_stale_end_resume(object(), config={}, snapshot=snapshot) is None


@pytest.mark.asyncio
async def test_terminal_owner_attention_never_restarts_in_same_invocation():
    state = {
        "verdict": "IMPL_OK",
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_UNKNOWN",
        "verify_stop_reason": "Owner Attention",
    }

    class UnexpectedGraph:
        async def aget_state(self, _config):
            raise AssertionError("terminal pause must not resume checkpoint")

    actual = await _maybe_continue_incomplete_verify_pipeline(
        UnexpectedGraph(), config={}, thread_id="verify-impl:demo",
        result=state, rewind_note=None,
    )
    assert actual is state


@pytest.mark.asyncio
async def test_owner_attention_is_terminal_even_without_scenario_verdict():
    snapshot = SimpleNamespace(next=(), values={
        "verdict": "IMPL_GAPS",
        "verify_stop_reason": "Owner Attention",
    })
    assert await plan_stale_end_resume(object(), config={}, snapshot=snapshot) is None


@pytest.mark.asyncio
async def test_plan_artifacts_pause_never_reenters_preflight_after_stale_end():
    snapshot = SimpleNamespace(
        next=(),
        values={
            "plan_artifact_status": "BLOCKED",
            "verify_stop_reason": "PLAN_ARTIFACTS_PAUSED",
            "verify_recovery_contract": (
                "COVERAGE_INPUT_STALE: specs/core/spec.md; "
                "producer=/opsx-spec-scenario-coverage mode=plan"
            ),
        },
    )
    # A terminal gate must not attempt subgraph checkpoint traversal or SDK work.
    assert await plan_stale_end_resume(object(), config={}, snapshot=snapshot) is None


@pytest.mark.asyncio
async def test_plan_artifacts_pause_never_retries_in_same_invocation():
    state = {
        "plan_artifact_status": "BLOCKED",
        "verify_stop_reason": "PLAN_ARTIFACTS_PAUSED",
        "verify_recovery_contract": "COVERAGE_INPUT_STALE: specs/core/spec.md",
    }

    class NoRestartGraph:
        async def aget_state(self, _config):
            raise AssertionError("blocked plan must never re-enter preflight")

    result = await _maybe_continue_incomplete_verify_pipeline(
        NoRestartGraph(), config={}, thread_id="verify-impl:demo",
        result=state, rewind_note=None,
    )
    assert result is state
    assert result["verify_recovery_contract"] == state["verify_recovery_contract"]
