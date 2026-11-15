"""Приёмка slice receipt против operative gate до SKIP reuse."""

from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path
from typing import Any, Mapping

from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)
from workflows.implementation.slice_receipt import receipt_json_path


def _receipt_requires_operative_acceptance(stage: str, receipt: Mapping[str, Any]) -> bool:
    """Полный machine receipt требует PASS operative; stub — только digest migrate."""
    if stage == STAGE_APPLY_RED:
        status = str(receipt.get("status") or "")
        if status in ("RED_PROVEN", "ALREADY_SATISFIED"):
            return True
        return bool(receipt.get("schema") == "opsx.apply_red" and receipt.get("test_commands"))
    if stage == STAGE_APPLY_SCAFFOLD:
        return bool(receipt.get("schema") == "opsx.apply_scaffold")
    if stage == STAGE_APPLY_IMPLEMENT:
        return bool(receipt.get("schema") == "opsx.apply_implement")
    if stage == STAGE_APPLY_GREEN:
        return bool(receipt.get("schema") == "opsx.apply_green" and receipt.get("frozen_test_content_identity"))
    return False


def repair_blocks_receipt_reuse(
    state: Mapping[str, Any] | None,
    *,
    change_id: str,
    slice_id: str,
    stage: str,
) -> bool:
    """Активный machine repair / arbiter на стадии запрещает обычный SKIP."""
    if state is None:
        return False
    if int(state.get("implementation_hop_machine_repair_count") or 0) > 0:
        return True
    operative = state.get("apply_red_operative_validation")
    if stage == STAGE_APPLY_RED and isinstance(operative, dict) and operative.get("ok") is False:
        return True
    target = str(state.get("implementation_arbiter_target_stage") or "").strip()
    if target == stage and str(state.get("implementation_arbiter_directive") or "").strip():
        return True
    if state.get("implementation_arbiter_pending") is True:
        return True
    _ = (change_id, slice_id)
    return False


def stage_receipt_contract_accepted(
    change_id: str,
    slice_id: str,
    stage: str,
    receipt: Mapping[str, Any],
    *,
    repo_root: Path,
) -> bool:
    """True, если receipt уже принят operative validator (или stub ещё не полный)."""
    if not _receipt_requires_operative_acceptance(stage, receipt):
        return True
    path = receipt_json_path(change_id, slice_id, stage, repo_root=repo_root)
    if stage == STAGE_APPLY_RED:
        validator = resource_path(repo_root, "opsx_tools", "opsx-apply-red.mjs")
        if not validator.is_file():
            return False
        from workflows.implementation.red_operative_validation import validate_red_operative

        return validate_red_operative(
            path,
            repo_root=repo_root,
            change_id=change_id,
            slice_id=slice_id,
        ).ok
    if stage == STAGE_APPLY_SCAFFOLD:
        from workflows.implementation.slice_stage_operative_validation import (
            validate_scaffold_operative_projected,
        )

        return validate_scaffold_operative_projected(path, repo_root=repo_root).ok
    if stage == STAGE_APPLY_IMPLEMENT:
        from workflows.implementation.slice_stage_operative_validation import (
            validate_implement_operative,
        )

        return validate_implement_operative(path, repo_root=repo_root).ok
    if stage == STAGE_APPLY_GREEN:
        from workflows.implementation.slice_stage_operative_validation import (
            validate_green_operative,
        )

        return validate_green_operative(path, repo_root=repo_root).ok
    return False
