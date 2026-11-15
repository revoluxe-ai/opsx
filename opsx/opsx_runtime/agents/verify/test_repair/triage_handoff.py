"""Согласование test_triage с предшествующим hop длительности (timeout/stall)."""

from __future__ import annotations

from typing import Any

from agents.state import GraphState
from agents.verify.static_triage.schemas import StaticTriageEvidenceRef
from agents.verify.test_triage.schemas import TestTriageResult


def _finding_id_from_state(state: GraphState, raw: dict[str, Any]) -> str:
    finding_id = str(raw.get("finding_id") or "")
    item = state.get("dispatcher_current_item")
    if isinstance(item, dict) and not finding_id:
        finding_id = str(item.get("finding_id") or "")
    return finding_id


def resolve_test_triage_for_repair(state: GraphState) -> dict[str, Any]:
    """Вернуть ``test_triage_result`` для repair или синтез из upstream hop.

    Маршруты без отдельного ``test_triage`` (``test_duration`` HANG_TEST,
    ``pytest_single_repro`` PASS → ``test_interaction`` → ``test_repair``) синтезируют
    классификацию ``TEST`` для executor.
    """
    triage_raw = state.get("test_triage_result")
    if isinstance(triage_raw, dict):
        return triage_raw

    interaction_raw = state.get("test_interaction_result")
    if isinstance(interaction_raw, dict):
        owner = str(interaction_raw.get("repair_owner") or "")
        if owner == "test_repair":
            finding_id = _finding_id_from_state(state, interaction_raw)
            rationale = str(interaction_raw.get("rationale") or "test_interaction")
            iclass = str(interaction_raw.get("classification") or "")
            promoted = TestTriageResult(
                finding_id=finding_id,
                classification="TEST",
                evidence_refs=StaticTriageEvidenceRef(),
                rationale=(
                    f"promoted from test_interaction ({iclass}): {rationale}"
                )[:2000],
                change_slice_status="NO_PATH",
            )
            return promoted.model_dump(mode="json")

    duration_raw = state.get("test_duration_result")
    if not isinstance(duration_raw, dict):
        raise ValueError("test_triage_result missing for test_repair")
    classification = str(duration_raw.get("classification") or "")
    if classification != "HANG_TEST":
        raise ValueError("test_triage_result missing for test_repair")
    finding_id = _finding_id_from_state(state, duration_raw)
    rationale = str(duration_raw.get("rationale") or "duration HANG_TEST")
    promoted = TestTriageResult(
        finding_id=finding_id,
        classification="TEST",
        evidence_refs=StaticTriageEvidenceRef(),
        rationale=f"promoted from test_duration: {rationale}"[:2000],
        change_slice_status="NO_PATH",
    )
    return promoted.model_dump(mode="json")
