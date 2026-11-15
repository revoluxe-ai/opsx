"""Runtime-агент Diagnostic Agent: causal repair unit после repair_order."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agents.adapters.abc_adapter import (
    agent_session_patch,
    resume_for_role,
    schema_format_reminder,
)
from agents.adapters.adapter_transport import adapter_transport_state
from agents.adapters.agent_stream import compose_agent_stream_label
from agents.abc_agent import ABCAgent
from agents.state import GraphState
from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import GroupErrorResult
from agents.verify.repair_order.schemas import RepairOrderResult
from agents.verify.diagnostic.enrichment import materialize_diagnostic_result
from agents.verify.diagnostic.input_pack import (
    build_diagnostic_package,
    package_json,
    write_diagnostic_findings_artifact,
)
from agents.verify.diagnostic.prompt.get_prompt import get_prompt
from agents.verify.diagnostic.schemas import (
    DiagnosticAnswer,
    DiagnosticResult,
    source_identity_from_repair_order,
)
from agents.verify.diagnostic.validation import (
    validate_anchor_is_queue_head,
    validate_no_regroup,
    validate_repair_unit_coverage,
)


_DIAGNOSTIC_READ_TOOLS = ("read", "grep", "glob", "ls")


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


def _load_repair_order(state: GraphState) -> RepairOrderResult | None:
    raw = state.get("repair_order_result")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("repair_order_result is not a dict in GraphState")
    return RepairOrderResult.model_validate(raw)


def _pause_patch(
    *,
    repair_order: RepairOrderResult | None,
    analysis: GroupErrorResult | None,
    pause_reason: str,
) -> dict[str, Any]:
    """Детерминированный PAUSE без LLM."""
    if repair_order is not None:
        source = source_identity_from_repair_order(repair_order)
    elif analysis is not None:
        from agents.verify.repair_order.schemas import source_identity_from_analysis

        ro_src = source_identity_from_analysis(analysis.analysis_identity)
        from agents.verify.diagnostic.schemas import DiagnosticSourceIdentity

        source = DiagnosticSourceIdentity(
            change_id=ro_src.change_id,
            execution_id=ro_src.execution_id,
            revision=ro_src.revision,
            identity_status=ro_src.identity_status,
            group_error_schema_version=ro_src.group_error_schema_version,
            repair_order_schema_version="1.1",
        )
    else:
        from agents.verify.diagnostic.schemas import DiagnosticSourceIdentity

        source = DiagnosticSourceIdentity(identity_status="PARTIAL")
    result = DiagnosticResult(
        status="PAUSE",
        source_identity=source,
        anchor_unit_id="",
        repair_unit=None,
        remaining_queue=[],
        pause_reason=pause_reason,
    )
    return {
        "diagnostic_result": result.model_dump(mode="json"),
        "diagnostic_agent_id": None,
        "diagnostic_run_id": None,
        "diagnostic_execution_status": "paused",
        "diagnostic_usage": None,
        "diagnostic_cost": None,
        "diagnostic_cost_status": "not_applicable",
        "diagnostic_billing_error": None,
        "diagnostic_usage_source": "deterministic",
        "diagnostic_validation_attempts": 0,
    }


def _per_finding_resolved_for_diagnostic(
    state: GraphState,
    repair_order: RepairOrderResult,
) -> tuple[set[str], dict[str, Any]]:
    """Resolved ids для per_finding diagnostic и опциональный сброс после failed batch recheck.

    После COMMITTED findings и ``batch_recheck_gates`` FULL_SUITE может снова FAIL, при этом
    ``dispatcher_resolved_finding_ids`` ещё полный — ``build_per_finding_diagnostic`` иначе падает.
  """
    from agents.verify.dispatcher.flatten_order import pending_finding_ids

    raw = state.get("dispatcher_resolved_finding_ids")
    resolved = (
        {str(x) for x in raw if x}
        if isinstance(raw, list)
        else set()
    )
    extra_patch: dict[str, Any] = {}
    batch_recheck = int(state.get("batch_recheck_count") or 0) > 0
    verdict = str(state.get("quality_gate_verdict") or "")
    if (
        batch_recheck
        and verdict == "FAIL"
        and not pending_finding_ids(repair_order, resolved=resolved)
    ):
        resolved = set()
        extra_patch = {
            "dispatcher_resolved_finding_ids": [],
            "dispatcher_pass_index": 0,
            "dispatcher_pass_queue": [],
            "dispatcher_outcomes": [],
        }
    return resolved, extra_patch


def _assess_pause(
    state: GraphState,
    repair_order: RepairOrderResult | None,
    analysis: GroupErrorResult,
) -> str | None:
    """Вернуть ``pause_reason`` или ``None`` если можно вызывать LLM."""
    from agents.verify.group_error.stale_skip_handoff import (
        stale_group_error_skip_should_pause,
    )

    if stale_group_error_skip_should_pause(state):
        return (
            "group_error hop skipped due to incomplete or invalid diagnostics input; "
            "diagnostic requires input_integrity COMPLETE"
        )
    if repair_order is None:
        return "repair_order_result missing from GraphState"
    if repair_order.status == "PAUSE":
        detail = repair_order.pause_reason or "repair_order PAUSE"
        return f"repair_order paused: {detail}"
    if str(state.get("repair_order_execution_status") or "") == "paused":
        return "repair_order_execution_status paused"
    if not repair_order.ordered_units:
        return "repair_order ordered_units empty; nothing to diagnose"
    if analysis.input_integrity.status == "INVALID":
        issues = analysis.input_integrity.issues
        detail = "; ".join(issues[:5]) if issues else "input_integrity INVALID"
        return f"group_error input_integrity INVALID: {detail}"
    if analysis.input_integrity.status == "PARTIAL":
        issues = analysis.input_integrity.issues
        detail = "; ".join(issues[:5]) if issues else "input_integrity PARTIAL"
        return f"group_error input_integrity PARTIAL: {detail}"
    return None


def validate_diagnostic_llm_output(
    payload: BaseModel,
    *,
    repair_order: RepairOrderResult,
    analysis: GroupErrorResult,
) -> None:
    """Post-parse инварианты diagnostic до materialize в ``DiagnosticResult``.

    Вызывается из ``ABCAdapter.run``; при ошибке агент получает resume с текстом исключения.

    Args:
        payload: Распарсенный ``DiagnosticAnswer`` из ответа LLM.
        repair_order: Вход repair order hop (ordered_units, позиции).
        analysis: Triage group_error для ``validate_no_regroup``.

    Raises:
        ValueError: Нарушение anchor, coverage или regroup.
        TypeError: ``payload`` не ``DiagnosticAnswer``.
    """
    if not isinstance(payload, DiagnosticAnswer):
        raise TypeError(f"expected DiagnosticAnswer, got {type(payload)!r}")
    candidate = materialize_diagnostic_result(payload, repair_order, analysis)
    validate_anchor_is_queue_head(candidate, repair_order)
    validate_repair_unit_coverage(candidate, repair_order)
    validate_no_regroup(candidate, repair_order, analysis)


class DiagnosticAgent(ABCAgent):
    """Causal diagnosis: один Repair Unit (anchor + confirmed linkage), без executors.

    Handoff для Dispatcher: ``repair_unit`` → один групповой triage и sequential group repair.
    """

    async def run(self, state: GraphState) -> dict[str, Any]:
        """Построить ``diagnostic_result`` из repair_order + analysis и ответа LLM."""
        change_id = str(state.get("change_id") or "").strip()
        if not change_id:
            raise ValueError("change_id is required in GraphState")
        repo = str(state["repository_path"])
        workspace = str(state.get("workspace") or repo)

        analysis = _load_analysis(state)
        repair_order = _load_repair_order(state)
        pause_reason = _assess_pause(state, repair_order, analysis)
        if pause_reason is not None:
            return _pause_patch(
                repair_order=repair_order,
                analysis=analysis,
                pause_reason=pause_reason,
            )
        assert repair_order is not None

        from agents.verify.dispatch_mode import resolve_verify_dispatch_mode
        from agents.verify.diagnostic.per_finding import build_per_finding_diagnostic

        if resolve_verify_dispatch_mode(state) == "per_finding" and analysis is not None:
            resolved, resolved_patch = _per_finding_resolved_for_diagnostic(
                state,
                repair_order,
            )
            try:
                parsed = build_per_finding_diagnostic(
                    repair_order,
                    analysis,
                    resolved=resolved,
                )
            except ValueError as exc:
                if "no pending findings" not in str(exc):
                    raise
                return {
                    **_pause_patch(
                        repair_order=repair_order,
                        analysis=analysis,
                        pause_reason=str(exc),
                    ),
                    **resolved_patch,
                }
            return {
                "diagnostic_result": parsed.model_dump(mode="json"),
                "diagnostic_execution_status": "PER_FINDING_DETERMINISTIC",
                "verify_dispatch_mode": "per_finding",
                **resolved_patch,
            }

        stream_log = state.get("diagnostic_stream_log") or state.get("stream_log")
        from agents.verify.diagnostic.input_pack import build_diagnostic_findings_payload

        findings_payload = build_diagnostic_findings_payload(repair_order, analysis)
        _ = write_diagnostic_findings_artifact(
            repository_path=repo,
            change_id=change_id,
            repair_order=repair_order,
            analysis=analysis,
            stream_log=str(stream_log) if stream_log else None,
        )
        package = build_diagnostic_package(
            repair_order,
            analysis,
            findings_embed=findings_payload,
        )
        prompt = get_prompt(diagnostic_package_json=package_json(package))

        outcome = await self._adapter.run(
            adapter_transport_state(
                state,
                stream_log=stream_log,
                stream_label=compose_agent_stream_label(state, "diagnostic"),
            ),
            prompt=prompt,
            agent_key="diagnostic",
            tools=_DIAGNOSTIC_READ_TOOLS,
            resume_agent_id=resume_for_role(state, "diagnostic_agent_id"),
            payload_model=DiagnosticAnswer,
            format_instructions=schema_format_reminder(DiagnosticAnswer),
            max_validation_attempts=self.validation_max_attempts,
            post_parse_validate=lambda parsed: validate_diagnostic_llm_output(
                parsed,
                repair_order=repair_order,
                analysis=analysis,
            ),
        )
        answer: DiagnosticAnswer = outcome.parsed  # type: ignore[assignment]
        parsed = materialize_diagnostic_result(answer, repair_order, analysis)
        from bug_log.context import active_journal
        from bug_log.mint import record_hop

        journal = getattr(self, "_bug_journal", None) or active_journal()
        bug_ids = dict(state.get("bug_ids") or {})
        for finding in analysis.findings:
            await record_hop(
                journal,
                bug_id=bug_ids.get(finding.finding_id) or finding.bug_id,
                summary="diagnostic",
                status="CLASSIFIED",
                role="diagnostic",
            )

        return {
            "diagnostic_result": parsed.model_dump(mode="json"),
            **agent_session_patch(
                state,
                agent_id=outcome.agent_id,
                agent_id_key="diagnostic_agent_id",
            ),
            "diagnostic_run_id": outcome.run_id,
            "diagnostic_execution_status": outcome.status,
            "diagnostic_usage": outcome.usage,
            "diagnostic_cost": outcome.cost,
            "diagnostic_cost_status": outcome.cost_status,
            "diagnostic_billing_error": outcome.billing_error,
            "diagnostic_usage_source": outcome.usage_source,
            "diagnostic_validation_attempts": outcome.validation_attempts,
        }
