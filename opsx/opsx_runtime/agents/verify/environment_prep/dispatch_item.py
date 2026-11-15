"""Synthetic dispatch item для commit после environment_prep."""

from __future__ import annotations

from agents.state import GraphState
from agents.verify.environment_prep.schemas import EnvironmentPrepCohorts


def build_environment_prep_dispatch_item(
    state: GraphState,
    *,
    cohorts: EnvironmentPrepCohorts,
    summary: str,
) -> dict[str, object]:
    """Item для ``run_verify_finding_commit`` вне dispatch_cycle."""
    finding_ids: list[str] = []
    for cohort in cohorts.cohorts:
        finding_ids.extend(cohort.finding_ids)
    observation = summary.strip() or "environment_prep batch fix"
    tail = ""
    diag = state.get("quality_gate_diagnostics")
    if isinstance(diag, dict) and isinstance(diag.get("pytest"), list):
        lines = [str(x) for x in diag["pytest"][:8] if x]
        if lines:
            tail = "\n".join(lines)
    obs = f"{observation}\n\n{tail}"[:4000]
    return {
        "finding_id": "ENV-PREP",
        "observation": obs,
        "path": "tests",
        "source_tool": "pytest",
        "affected_modules": ["tests"],
        "group_finding_ids": finding_ids[:64] or ["ENV-PREP"],
        "repair_workflow": "WF_PYTEST_COLLECTION",
    }
