"""Runtime-агент Repair Order Agent: упорядочивание repair units после group_error."""

from __future__ import annotations

from typing import Any

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.abc_agent import ABCAgent
from agents.state import GraphState
from agents.verify.group_error.gate_only_triage import resolve_repair_order_llm_enabled
from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.group_error.validation import validate_finding_coverage
from agents.verify.repair_order.input_pack import (
    build_repair_order_package,
    package_json,
)
from agents.verify.repair_order.prompt.get_prompt import get_prompt
from agents.verify.repair_order.schemas import (
    RepairOrderAnswer,
    RepairOrderResult,
    SourceIdentity,
    source_identity_from_analysis,
)
from agents.verify.repair_order.enrichment import attach_finding_workflows
from agents.verify.repair_order.harness_priority import prioritize_harness_units
from agents.verify.repair_order.sort_units import (
    build_baseline_ordered_units,
    sort_units_by_ordered_ids,
)
from agents.verify.repair_order.validation import (
    validate_no_regroup,
    validate_positions,
    validate_unit_coverage,
)
from agents.verify.environment_prep.queue_shrink import (
    resolved_finding_ids_from_state,
    shrink_group_error_for_resolved,
)

_DETERMINISTIC_GROUP_ERROR_STATUSES = frozenset(
    {
        "deterministic_gate_only",
        "fallback_partition",
        "skipped_integrity",
        "skipped_input_integrity",
    },
)


def _load_analysis(state: GraphState) -> GroupErrorResult:
    raw = state.get("analysis_result")
    if not isinstance(raw, dict):
        raise ValueError("analysis_result missing or not a dict in GraphState")
    parsed = GroupErrorResult.model_validate(raw)
    needs_full_normalize = (
        parsed.schema_version == "1.4"
        or bool(parsed.finding_groups)
        or bool(parsed.ungrouped_finding_ids)
        or (parsed.schema_version == "1.5" and not parsed.grouped_errors and parsed.findings)
    )
    if needs_full_normalize:
        return normalize_group_error_result(parsed)
    return normalize_group_error_result(parsed, fill_coverage_gaps=False)


def _missing_analysis_handoff_patch(state: GraphState) -> dict[str, Any]:
    """Детерминированный PAUSE, если repair_order вызван без group_error handoff."""
    change_id = str(state.get("change_id") or "").strip() or None
    result = RepairOrderResult(
        status="PAUSE",
        source_identity=SourceIdentity(change_id=change_id, identity_status="PARTIAL"),
        ordered_units=[],
        confirmed_dependencies=[],
        pause_reason=(
            "analysis_result missing or not a dict in GraphState; "
            "repair_order requires group_error handoff"
        ),
    )
    return {
        "repair_order_result": result.model_dump(mode="json"),
        "repair_order_agent_id": None,
        "repair_order_run_id": None,
        "repair_order_execution_status": "paused",
        "repair_order_usage": None,
        "repair_order_cost": None,
        "repair_order_cost_status": "not_applicable",
        "repair_order_billing_error": None,
        "repair_order_usage_source": "deterministic",
        "repair_order_validation_attempts": 0,
    }


def _pause_patch(
    *,
    analysis: GroupErrorResult,
    pause_reason: str,
) -> dict[str, Any]:
    """Детерминированный PAUSE без LLM."""
    result = RepairOrderResult(
        status="PAUSE",
        source_identity=source_identity_from_analysis(
            analysis.analysis_identity,
            group_error_schema_version=analysis.schema_version,
        ),
        ordered_units=[],
        confirmed_dependencies=[],
        pause_reason=pause_reason,
    )
    return {
        "repair_order_result": result.model_dump(mode="json"),
        "repair_order_agent_id": None,
        "repair_order_run_id": None,
        "repair_order_execution_status": "paused",
        "repair_order_usage": None,
        "repair_order_cost": None,
        "repair_order_cost_status": "not_applicable",
        "repair_order_billing_error": None,
        "repair_order_usage_source": "deterministic",
        "repair_order_validation_attempts": 0,
    }


def _should_use_baseline_order(state: GraphState) -> bool:
    """Детерминированный порядок grouped_errors без LLM (verify highway default)."""
    ge_status = str(state.get("group_error_execution_status") or "")
    if ge_status in _DETERMINISTIC_GROUP_ERROR_STATUSES:
        return True
    return not resolve_repair_order_llm_enabled(state)


def _analysis_for_repair_order(state: GraphState, analysis: GroupErrorResult) -> GroupErrorResult:
    """Учесть findings, снятые environment_prep, до baseline sort."""
    resolved = resolved_finding_ids_from_state(state)
    if not resolved:
        return analysis
    return shrink_group_error_for_resolved(analysis, resolved)


def _sorted_baseline_patch(analysis: GroupErrorResult, *, state: GraphState) -> dict[str, Any]:
    """Собрать ``repair_order_result`` в baseline-порядке без adapter."""
    analysis = _analysis_for_repair_order(state, analysis)
    baseline_units = build_baseline_ordered_units(analysis)
    parsed = RepairOrderResult(
        status="SORTED",
        source_identity=source_identity_from_analysis(
            analysis.analysis_identity,
            group_error_schema_version=analysis.schema_version,
        ),
        ordered_units=list(baseline_units),
        confirmed_dependencies=[],
        pause_reason=None,
    )
    validate_unit_coverage(parsed, analysis)
    validate_no_regroup(parsed, analysis)
    validate_positions(parsed)
    parsed = attach_finding_workflows(parsed, analysis)
    prioritized = prioritize_harness_units(parsed.ordered_units)
    parsed = parsed.model_copy(update={"ordered_units": prioritized})
    return {
        "repair_order_result": parsed.model_dump(mode="json"),
        "repair_order_agent_id": None,
        "repair_order_run_id": None,
        "repair_order_execution_status": "deterministic_baseline",
        "repair_order_usage": None,
        "repair_order_cost": None,
        "repair_order_cost_status": "not_applicable",
        "repair_order_billing_error": None,
        "repair_order_usage_source": "deterministic",
        "repair_order_validation_attempts": 0,
    }


def _assess_pause(state: GraphState, analysis: GroupErrorResult) -> str | None:
    """Вернуть ``pause_reason`` или ``None`` если можно идти в repair."""
    ge_status = str(state.get("group_error_execution_status") or "")
    if ge_status in _DETERMINISTIC_GROUP_ERROR_STATUSES:
        if not analysis.findings:
            return None
        try:
            validate_finding_coverage(analysis)
        except ValueError as exc:
            return f"group_error finding coverage broken: {exc}"
        return None
    from agents.verify.group_error.stale_skip_handoff import (
        stale_group_error_skip_should_pause,
    )

    if stale_group_error_skip_should_pause(state):
        return (
            "group_error hop skipped due to incomplete or invalid diagnostics input; "
            "repair order requires input_integrity COMPLETE"
        )
    if analysis.input_integrity.status == "INVALID":
        issues = analysis.input_integrity.issues
        detail = "; ".join(issues[:5]) if issues else "input_integrity INVALID"
        return f"group_error input_integrity INVALID: {detail}"
    if analysis.input_integrity.status == "PARTIAL":
        issues = analysis.input_integrity.issues
        detail = "; ".join(issues[:5]) if issues else "input_integrity PARTIAL"
        return f"group_error input_integrity PARTIAL: {detail}"
    try:
        validate_finding_coverage(analysis)
    except ValueError as exc:
        return f"group_error finding coverage broken: {exc}"
    return None


class RepairOrderAgent(ABCAgent):
    """Read-only sort: GROUP/UNGROUPED units из triage без regroup и без repair execution."""

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Построить ``repair_order_result`` из ``analysis_result`` и ответа LLM."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)

        raw_analysis = state.get("analysis_result")
        if not isinstance(raw_analysis, dict):
            return _missing_analysis_handoff_patch(state)

        analysis = _load_analysis(state)
        pause_reason = _assess_pause(state, analysis)
        if pause_reason is not None:
            return _pause_patch(analysis=analysis, pause_reason=pause_reason)

        if _should_use_baseline_order(state):
            return _sorted_baseline_patch(analysis, state=state)

        package = build_repair_order_package(analysis)
        prompt = get_prompt(repair_order_package_json=package_json(package))

        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=state.get("repair_order_stream_log") or state.get("stream_log"),
                stream_label=compose_agent_stream_label(state, "repair_order"),
            ),
            prompt=prompt,
            agent_key="repair_order",
            tools=(),
            resume_agent_id=resume_for_role(state, "repair_order_agent_id"),
            payload_model=RepairOrderAnswer,
            format_instructions=schema_format_reminder(RepairOrderAnswer),
            max_validation_attempts=self.validation_max_attempts,
        )
        answer: RepairOrderAnswer = outcome.parsed  # type: ignore[assignment]
        baseline_units = build_baseline_ordered_units(analysis)
        ordered_units = sort_units_by_ordered_ids(
            baseline_units,
            answer.ordered_unit_ids,
        )
        parsed = RepairOrderResult(
            status="SORTED",
            source_identity=source_identity_from_analysis(
                analysis.analysis_identity,
                group_error_schema_version=analysis.schema_version,
            ),
            ordered_units=ordered_units,
            confirmed_dependencies=[],
            pause_reason=None,
        )
        validate_unit_coverage(parsed, analysis)
        validate_no_regroup(parsed, analysis)
        validate_positions(parsed)
        parsed = attach_finding_workflows(parsed, analysis)
        prioritized = prioritize_harness_units(parsed.ordered_units)
        parsed = parsed.model_copy(update={"ordered_units": prioritized})

        return {
            "repair_order_result": parsed.model_dump(mode="json"),
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="repair_order_agent_id",
            ),
            "repair_order_run_id": outcome.run_id,
            "repair_order_execution_status": outcome.status,
            "repair_order_usage": outcome.usage,
            "repair_order_cost": outcome.cost,
            "repair_order_cost_status": outcome.cost_status,
            "repair_order_billing_error": outcome.billing_error,
            "repair_order_usage_source": outcome.usage_source,
            "repair_order_validation_attempts": outcome.validation_attempts,
        }
