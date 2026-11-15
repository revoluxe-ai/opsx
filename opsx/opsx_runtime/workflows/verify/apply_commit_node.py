"""Commit hop после ``ApplyAgent`` (reuse ``CommitAgent``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import ABCAdapter
from agents.common.commit_agent import CommitAgent
from agents.state import GraphState
from workflows.repair.schemas import FixEvidence, ModelSelection, RepairState
from agents.verify.coverage_activation.green_resume import (
    patch_coverage_activation_apply_code_changed,
)
from workflows.verify.green_loop import (
    commit_outcome_implies_code_changed,
    patch_workflow_code_changed,
)


async def run_verify_apply_commit(
    state: GraphState,
    *,
    repair_adapter: ABCAdapter | None = None,
) -> dict[str, Any]:
    """Закоммитить ``verify_finding_fix`` от apply-hop через ``CommitAgent``."""
    fix_raw = state.get("verify_finding_fix")
    if not isinstance(fix_raw, dict):
        return {
            "commit_status": "SKIPPED_NO_FIX",
            "verify_finding_commit_outcome": {"status": "SKIPPED_NO_FIX"},
            "verify_check_circle_code_changed": False,
        }

    change_id = str(state.get("change_id") or "verify").strip()
    workspace = str(state.get("workspace") or state["repository_path"])
    model_raw = state.get("model")
    model = (
        ModelSelection.model_validate(model_raw)
        if isinstance(model_raw, dict)
        else ModelSelection()
    )
    repair_state = RepairState(
        mission_id=f"verify-apply-{change_id}",
        repository_path=str(state["repository_path"]),
        workspace=workspace,
        output_dir=str(
            Path(workspace) / "opsx" / "runs" / "verify" / "impl-apply-commit" / change_id,
        ),
        model=model,
        status="ACTIVE",
        current_stage="fix",
        owner_go=True,
        fix=FixEvidence.model_validate(fix_raw),
    )
    commit_agent = CommitAgent(adapter=repair_adapter)
    patch = await commit_agent.run(repair_state.model_dump(mode="json"))
    result = {
        **patch,
        "verify_finding_commit_outcome": {
            "commit_status": patch.get("commit_status"),
            "commit_sha": patch.get("commit_sha"),
            "commit_files": patch.get("commit_files"),
        },
    }
    if commit_outcome_implies_code_changed(result):
        result.update(patch_workflow_code_changed())
        result.update(patch_coverage_activation_apply_code_changed())
    result["verify_check_circle_code_changed"] = commit_outcome_implies_code_changed(result)
    return result
