"""Commit hop через существующий ``CommitAgent`` (static и test path)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import ABCAdapter
from agents.common.commit_agent import CommitAgent
from agents.state import GraphState
from workflows.verify.dispatch_cycle.repair_state import (
    build_verify_repair_state,
    merge_fix_into_repair_state,
)
from workflows.verify.dispatch_cycle.commit_revision import repair_files_revision_key
from workflows.verify.green_loop import (
    commit_outcome_implies_code_changed,
    patch_workflow_code_changed,
)
from workflows.repo_paths import resolve_opsx_repository_root


def _commit_revision_map(state: GraphState) -> dict[str, str]:
    raw = state.get("finding_commit_revision_by_finding")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if k and v}


async def run_verify_finding_commit(
    state: GraphState,
    *,
    repair_adapter: ABCAdapter | None = None,
) -> dict[str, Any]:
    """Закоммитить ``verify_finding_fix`` через ``agents.common.commit_agent.CommitAgent``."""
    item = state.get("dispatcher_current_item")
    if not isinstance(item, dict):
        raise ValueError("dispatcher_current_item missing for finding_commit")

    fix = state.get("verify_finding_fix")
    if not isinstance(fix, dict):
        return {
            "commit_status": "SKIPPED_NO_FIX",
            "verify_finding_commit_outcome": {"status": "SKIPPED_NO_FIX"},
        }

    files = fix.get("files_changed")
    if not isinstance(files, list) or not files:
        return {
            "commit_status": "SKIPPED_NO_FIX",
            "verify_finding_commit_outcome": {"status": "SKIPPED_NO_FIX"},
        }
    finding_id = str(item.get("finding_id") or "")
    repo_raw = str(state.get("repository_path") or state.get("workspace") or ".")
    repo_root = resolve_opsx_repository_root(Path(repo_raw))
    revision_key = repair_files_revision_key(repo_root, [str(f) for f in files])
    prior = _commit_revision_map(state)
    if finding_id and prior.get(finding_id) == revision_key:
        return {
            "commit_status": "SKIPPED_ALREADY_COMMITTED",
            "verify_finding_commit_outcome": {
                "status": "SKIPPED_ALREADY_COMMITTED",
                "finding_id": finding_id,
                "revision_key": revision_key,
            },
            "finding_commit_result": {
                "status": "SKIPPED_ALREADY_COMMITTED",
                "finding_id": finding_id,
            },
        }

    repair_state = merge_fix_into_repair_state(
        build_verify_repair_state(state, item),
        fix,
    )
    commit_agent = CommitAgent(adapter=repair_adapter)
    patch = await commit_agent.run(repair_state.model_dump(mode="json"))
    commit_status = str(patch.get("commit_status") or "UNKNOWN")
    item = state.get("dispatcher_current_item")
    finding_id = (
        str(item.get("finding_id") or "")
        if isinstance(item, dict)
        else ""
    )
    result: dict[str, Any] = {
        **patch,
        "commit_status": commit_status,
        "verify_finding_commit_outcome": {
            "status": commit_status,
            "commit_status": commit_status,
            "commit_sha": patch.get("commit_sha"),
            "commit_files": patch.get("commit_files"),
            "commit_subject": patch.get("commit_subject"),
            "commit_body": patch.get("commit_body"),
            "finding_id": finding_id or None,
            "error": patch.get("error"),
        },
        "finding_commit_result": {
            "status": commit_status,
            "finding_id": finding_id or None,
            "commit_sha": patch.get("commit_sha"),
        },
    }
    if commit_outcome_implies_code_changed(result):
        result.update(patch_workflow_code_changed())
    if finding_id and commit_status == "COMMITTED":
        updated_map = dict(prior)
        updated_map[finding_id] = revision_key
        result["finding_commit_revision_by_finding"] = updated_map
        result["verify_finding_commit_revision_key"] = revision_key
    return result


# Back-compat alias для тестов/импортов
run_verify_static_commit = run_verify_finding_commit
