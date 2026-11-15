"""Детерминированный triage group_error из quality_gate_diagnostics (без LLM)."""

from __future__ import annotations

import os
from typing import Any, Literal

from agents.state import GraphState
from agents.verify.group_error.diagnostics_input_integrity import (
    input_integrity_from_gate_diagnostics,
)
from agents.verify.group_error.findings_from_diagnostics import (
    build_findings_from_diagnostics,
)
from agents.verify.group_error.normalize_triage import normalize_group_error_result
from agents.verify.group_error.schemas import (
    AnalysisIdentity,
    GroupErrorResult,
    InputIntegrity,
    TriageSummary,
)
from agents.verify.group_error.validation import recompute_triage_summary


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def resolve_group_error_llm_enabled(state: GraphState) -> bool:
    """Вернуть True, если разрешён LLM-hop group_error (opt-in)."""
    if _truthy_env("OPSX_GROUP_ERROR_LLM"):
        return True
    mode = state.get("group_error_mode")
    if isinstance(mode, str) and mode.strip().lower() == "llm":
        return True
    return False


def resolve_repair_order_llm_enabled(state: GraphState) -> bool:
    """Вернуть True, если разрешён LLM-hop repair_order (opt-in)."""
    if _truthy_env("OPSX_REPAIR_ORDER_LLM"):
        return True
    mode = state.get("repair_order_mode")
    if isinstance(mode, str) and mode.strip().lower() == "llm":
        return True
    return False


def _diagnostics_dict(state: GraphState) -> dict[str, Any] | None:
    diagnostics = state.get("quality_gate_diagnostics")
    if isinstance(diagnostics, dict):
        return diagnostics
    return None


def findings_from_gate_state(state: GraphState) -> list:
    """Findings из diagnostics с учётом pytest classifier labels."""
    from agents.verify.pytest_error_classifier.merge import apply_classifier_label_overrides

    diagnostics = _diagnostics_dict(state)
    findings = build_findings_from_diagnostics(diagnostics)
    return apply_classifier_label_overrides(findings, state)


def build_gate_only_group_error_result(
    *,
    change_id: str,
    execution_id: str | None,
    state: GraphState,
    input_integrity: InputIntegrity | None = None,
    identity_status: Literal["VERIFIED", "PARTIAL", "MISMATCH"] = "VERIFIED",
) -> GroupErrorResult:
    """Собрать ``GroupErrorResult``: 1 finding → 1 singleton ``grouped_errors`` (без LLM)."""
    if input_integrity is None:
        input_integrity = input_integrity_from_gate_diagnostics(_diagnostics_dict(state))
    findings = findings_from_gate_state(state)
    ungrouped = [f.finding_id for f in findings]
    raw = GroupErrorResult(
        analysis_identity=AnalysisIdentity(
            change_id=change_id,
            execution_id=execution_id,
            identity_status=identity_status,
        ),
        input_integrity=input_integrity,
        ungrouped_finding_ids=ungrouped,
        findings=findings,
        triage_summary=TriageSummary(
            findings_total=len(findings),
            ungrouped_total=len(ungrouped),
        ),
    )
    normalized = normalize_group_error_result(raw)
    return recompute_triage_summary(normalized)


def deterministic_group_error_handoff_patch(
    analysis: GroupErrorResult,
    *,
    execution_status: str,
    owner_attention: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Стандартный state patch после gate-only / fallback group_error."""
    patch: dict[str, Any] = {
        "analysis_result": analysis.model_dump(mode="json"),
        "group_error_agent_id": None,
        "group_error_run_id": None,
        "group_error_execution_status": execution_status,
        "group_error_usage": None,
        "group_error_cost": None,
        "group_error_cost_status": "not_applicable",
        "group_error_billing_error": None,
        "group_error_usage_source": "deterministic",
        "group_error_validation_attempts": 0,
    }
    if owner_attention:
        patch["owner_attention"] = owner_attention
    patch.update(extra)
    return patch


__all__ = [
    "build_gate_only_group_error_result",
    "deterministic_group_error_handoff_patch",
    "findings_from_gate_state",
    "resolve_group_error_llm_enabled",
    "resolve_repair_order_llm_enabled",
]
