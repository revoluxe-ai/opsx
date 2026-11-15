"""Слияние HIGH labels classifier с findings group_error."""

from __future__ import annotations

from typing import Any, Mapping

from agents.verify.group_error.repair_workflow import classify_repair_workflow
from agents.verify.group_error.schemas import Finding, RepairWorkflowKind
from agents.verify.pytest_error_classifier.schemas import PytestErrorClassifierResult


def _workflow_for_axis(axis: str) -> RepairWorkflowKind | None:
    if axis == "BUILD":
        return "WF_PYTEST_COLLECTION"
    if axis == "TEST":
        return "WF_PYTEST_FAILURE"
    return None


def apply_classifier_label_overrides(
    findings: list[Finding],
    state: Mapping[str, Any],
) -> list[Finding]:
    """Переопределить ``repair_workflow`` для pytest findings при HIGH confidence."""
    raw = state.get("pytest_error_classifier_result")
    if not isinstance(raw, dict):
        return findings
    parsed = PytestErrorClassifierResult.model_validate(raw)
    overrides: dict[str, RepairWorkflowKind] = {}
    for label in parsed.labels:
        if label.confidence != "HIGH":
            continue
        wf = _workflow_for_axis(label.axis)
        if wf is not None:
            overrides[label.finding_id] = wf
    if not overrides:
        return findings
    out: list[Finding] = []
    for f in findings:
        wf = overrides.get(f.finding_id)
        if wf is not None and f.source_tool == "pytest":
            det = classify_repair_workflow(source_tool="pytest", observation=f.observation)
            if det != wf:
                _ = state
            out.append(f.model_copy(update={"repair_workflow": wf}))
        else:
            out.append(f)
    return out


def merge_pytest_classifier_labels_patch(state: Mapping[str, Any]) -> dict[str, Any]:
    """No-op patch node: classifier result уже в state; group_error читает при triage."""
    _ = state
    return {}
