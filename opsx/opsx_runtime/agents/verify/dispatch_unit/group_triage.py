"""Групповой triage Repair Unit через существующие Static/Test Triage adapters."""

from __future__ import annotations

import json
from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.state import GraphState
from agents.verify.dispatch_unit.queue_items import classify_unit_workflow_mix, is_repair_unit_item
from agents.verify.dispatch_unit.schemas import RepairUnitTriageResult
from agents.verify.dispatch_unit.split_heuristics import try_heuristic_causal_groups
from agents.verify.dispatch_unit.validation import validate_repair_unit_triage_partition
from agents.verify.static_triage.opsx_context import build_bounded_opsx_context
from agents.verify.static_triage.prompt.unit_repair_triage import format_unit_repair_triage_prompt


def build_unit_triage_input_pack(
    *,
    repository_path: str,
    unit_item: dict[str, Any],
    state: GraphState,
) -> dict[str, Any]:
    """JSON-пакет для группового triage (все findings repair unit)."""
    anchor = str(unit_item.get("anchor_unit_id") or "")
    embedded = list(unit_item.get("findings") or [])
    anchor_finding = next(
        (x for x in embedded if isinstance(x, dict) and x.get("finding_id") == anchor),
        embedded[0] if embedded else {},
    )
    opsx_context, context_manifest = build_bounded_opsx_context(
        state,
        item=anchor_finding if isinstance(anchor_finding, dict) else None,
    )
    from agents.verify.dispatch_unit.split_heuristics import (
        infer_finding_repair_lane,
        primary_exception_token,
    )

    per_finding_signals: list[dict[str, Any]] = []
    for row in embedded:
        if not isinstance(row, dict):
            continue
        fid = str(row.get("finding_id") or "")
        lane = infer_finding_repair_lane(row)
        per_finding_signals.append(
            {
                "finding_id": fid,
                "source_tool": row.get("source_tool"),
                "repair_workflow": row.get("repair_workflow"),
                "primary_exception": primary_exception_token(str(row.get("observation") or "")),
                "classification": lane[0],
                "repair_route_hint": lane[1],
                "observation_excerpt": str(row.get("observation") or "")[:400],
            },
        )

    return {
        "repair_unit": {
            "repair_unit_key": unit_item.get("repair_unit_key"),
            "anchor_unit_id": anchor,
            "finding_ids": list(unit_item.get("finding_ids") or []),
            "findings": embedded,
            "root_cause_summary": unit_item.get("root_cause_summary"),
            "change_boundaries": unit_item.get("change_boundaries"),
        },
        "workflow_mix": classify_unit_workflow_mix(unit_item),
        "per_finding_signals": per_finding_signals,
        "diagnostic_note": (
            "repair_unit.root_cause_summary is a hypothesis only; "
            "partition finding_ids by evidence, not by diagnostic merge"
        ),
        "opsx_context": opsx_context,
        "context_manifest": context_manifest,
        "repository_path": repository_path,
    }


async def run_repair_unit_group_triage(
    state: GraphState,
    *,
    adapter: Any,
    agent_name: str,
    agent_id_key: str,
    stream_log_key: str,
) -> dict[str, Any]:
    """Вызвать LLM групповой triage для ``dispatcher_current_item`` repair unit."""
    unit_item = state.get("dispatcher_current_item")
    if not is_repair_unit_item(unit_item if isinstance(unit_item, dict) else None):
        raise ValueError("dispatcher_current_item must be repair_unit for group triage")

    assert isinstance(unit_item, dict)
    mix = classify_unit_workflow_mix(unit_item)
    expected_ids = list(unit_item.get("finding_ids") or [])
    anchor = str(unit_item.get("anchor_unit_id") or (expected_ids[0] if expected_ids else ""))
    key = str(unit_item.get("repair_unit_key") or "")
    parsed_agent = "test_triage" if mix == "test" else "static_triage"

    heuristic_groups = try_heuristic_causal_groups(unit_item)
    if heuristic_groups is not None:
        heuristic = RepairUnitTriageResult(
            agent=parsed_agent,
            repair_unit_key=key,
            anchor_finding_id=anchor,
            status="GROUPED",
            causal_groups=heuristic_groups,
        )
        validate_repair_unit_triage_partition(
            heuristic,
            expected_finding_ids=expected_ids,
        )
        groups = [g.model_dump(mode="json") for g in heuristic.causal_groups]
        patch: dict[str, Any] = {
            "dispatch_unit_triage_result": heuristic.model_dump(mode="json"),
            "dispatch_causal_groups": groups,
            "dispatcher_unit_triage_complete": True,
            "dispatcher_group_index": 0,
            "dispatcher_group_phase": "repair",
            "verify_dispatch_path": "test" if mix == "test" else "static",
        }
        return patch

    repo = str(state["repository_path"])
    package = build_unit_triage_input_pack(
        repository_path=repo,
        unit_item=unit_item,
        state=state,
    )
    pack_json = json.dumps(package, ensure_ascii=False, indent=2)
    prompt = format_unit_repair_triage_prompt(input_pack_json=pack_json)

    def _post_validate(parsed: Any) -> None:
        if not isinstance(parsed, RepairUnitTriageResult):
            raise TypeError(f"expected RepairUnitTriageResult, got {type(parsed)!r}")
        parsed_agent = "test_triage" if mix == "test" else "static_triage"
        if parsed.agent != parsed_agent:
            raise ValueError(f"agent must be {parsed_agent!r}, got {parsed.agent!r}")
        validate_repair_unit_triage_partition(parsed, expected_finding_ids=expected_ids)

    outcome = await adapter.run(
        adapter_transport_state(
            state,
            stream_log=state.get(stream_log_key) or state.get("stream_log"),
            stream_label=compose_agent_stream_label(state, agent_name),
        ),
        prompt=prompt,
        tools=(),
        agent_key=agent_name,
        resume_agent_id=resume_for_role(state, agent_id_key),
        payload_model=RepairUnitTriageResult,
        format_instructions=schema_format_reminder(RepairUnitTriageResult),
        max_validation_attempts=3,
        post_parse_validate=_post_validate,
    )
    parsed: RepairUnitTriageResult = outcome.parsed  # type: ignore[assignment]
    groups = [g.model_dump(mode="json") for g in parsed.causal_groups]
    patch: dict[str, Any] = {
        "dispatch_unit_triage_result": parsed.model_dump(mode="json"),
        "dispatch_causal_groups": groups,
        "dispatcher_unit_triage_complete": True,
        "dispatcher_group_index": 0,
        "dispatcher_group_phase": "repair",
        **agent_session_patch(state, agent_id=outcome.agent_id, agent_id_key=agent_id_key),
    }
    if mix == "test":
        patch["verify_dispatch_path"] = "test"
    else:
        patch["verify_dispatch_path"] = "static"
    return patch


__all__ = ["build_unit_triage_input_pack", "run_repair_unit_group_triage"]
