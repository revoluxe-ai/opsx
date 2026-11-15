"""Завершение одного finding: outcome в dispatcher queue (static и test)."""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.dispatcher.agent import dispatcher_record_outcome


async def _finalize(
    state: GraphState,
    *,
    workflow_kind: str,
    triage_key: str,
    agent_label: str,
) -> dict[str, Any]:
    triage = state.get(triage_key)
    if not isinstance(triage, dict):
        triage = {}

    classification = triage.get("classification")
    repair_outcome = state.get("verify_finding_repair_outcome")
    commit_outcome = state.get("verify_finding_commit_outcome")

    outcome: dict[str, Any] = {
        "schema_version": "1.0",
        "agent": agent_label,
        "finding_id": triage.get("finding_id"),
        "classification": classification,
        "triage": triage,
        "repair": repair_outcome,
        "commit": commit_outcome,
        "commit_status": state.get("commit_status"),
    }
    if classification == "UNCLEAR":
        outcome["action"] = "SKIPPED_UNCLEAR"

    advance = await dispatcher_record_outcome(
        state,
        workflow_kind=workflow_kind,
        outcome=outcome,
    )
    return {
        **advance,
        "verify_finding_finalize_outcome": outcome,
    }


async def static_finding_finalize(state: GraphState) -> dict[str, Any]:
    """Записать outcome static path и вернуть управление dispatcher."""
    return await _finalize(
        state,
        workflow_kind="static",
        triage_key="static_triage_result",
        agent_label="static_dispatch_cycle",
    )


async def test_finding_finalize(state: GraphState) -> dict[str, Any]:
    """Записать outcome test path и вернуть управление dispatcher."""
    return await _finalize(
        state,
        workflow_kind="test",
        triage_key="test_triage_result",
        agent_label="test_dispatch_cycle",
    )
