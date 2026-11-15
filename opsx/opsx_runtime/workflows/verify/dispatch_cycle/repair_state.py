"""RepairState для verify static LOGIC hop (тонкий мост к bug-repair workflow)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.state import GraphState
from workflows.repair.schemas import (
    ClassificationPayload,
    Finding,
    ModelSelection,
    RedEvidence,
    RepairState,
)


def build_verify_repair_state(
    state: GraphState,
    item: dict[str, Any],
) -> RepairState:
    """Собрать RepairState для FIX hop существующего bug-repair workflow."""
    change_id = str(state.get("change_id") or "verify").strip()
    finding_id = str(item.get("finding_id") or "unknown")
    observation = str(item.get("observation") or "")
    path = item.get("path")
    line = item.get("line")
    if path and line:
        location = f"{path}:{line}"
    elif path:
        location = str(path)
    else:
        location = observation[:160] or finding_id

    active = Finding(
        id=finding_id,
        finding=observation or finding_id,
        location=location,
        severity="medium",
        evidence_paths=[str(path)] if path else [],
    )
    red = RedEvidence(
        status="RED_CONFIRMED",
        test_id=f"verify-static-{finding_id}",
        command=f"static-gate:{item.get('source_tool') or 'unknown'}",
        expected_failure=observation[:4000] or finding_id,
        actual_failure=observation[:4000] or finding_id,
        failure_match=True,
        notes="Synthetic RED from verify static finding for bug-repair FIX hop",
    )
    classification = ClassificationPayload(
        status="PASS",
        kind="IMPLEMENTATION_BUG",
        reason="Static triage classified LOGIC",
        evidence=(observation[:500] or finding_id),
        chosen_route="ROUTE_A",
    )
    workspace = str(state.get("workspace") or state["repository_path"])
    output_dir = str(
        Path(workspace)
        / "opsx"
        / "runs"
        / "verify"
        / "static-logic-repair"
        / change_id
        / finding_id,
    )
    model_raw = state.get("model")
    model = (
        ModelSelection.model_validate(model_raw)
        if isinstance(model_raw, dict)
        else ModelSelection()
    )
    return RepairState(
        mission_id=f"verify-static-{change_id}-{finding_id}",
        repository_path=str(state["repository_path"]),
        workspace=workspace,
        output_dir=output_dir,
        model=model,
        status="ACTIVE",
        current_stage="fix",
        active=active,
        classification=classification,
        red=red,
        owner_go=True,
    )


def merge_fix_into_repair_state(
    repair_state: RepairState,
    fix: dict[str, Any] | None,
) -> RepairState:
    """Добавить ``verify_static_fix`` перед commit hop."""
    if fix is None:
        return repair_state
    from workflows.repair.schemas import FixEvidence

    return repair_state.model_copy(update={"fix": FixEvidence.model_validate(fix)})
