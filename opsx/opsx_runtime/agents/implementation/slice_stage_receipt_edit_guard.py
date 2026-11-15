"""Fail-closed schema gate для slice stage receipt JSON сразу после edit tool."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from agents.config import ReadScopeViolation, paths_from_tool_args, to_repo_relative
from agents.implementation.apply_scaffold.scaffold_hop_visible_text import (
    format_scaffold_gate_errors_for_model,
)
from agents.implementation.prompt.slice_receipt_hop_tool import slice_receipt_hop_tool_block
from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
)
from workflows.implementation.slice_stage_operative_validation import (
    validate_green_operative,
    validate_implement_operative,
    validate_scaffold_operative_projected,
)
from workflows.implementation.red_operative_validation import validate_red_operative
from workflows.repo_paths import resolve_opsx_repository_root

SLICE_RECEIPT_SCHEMA_RULE = "slice_receipt_schema"

_STAGE_BY_MARKER: tuple[tuple[str, str], ...] = (
    ("/apply-scaffold/", STAGE_APPLY_SCAFFOLD),
    ("/apply-red/", STAGE_APPLY_RED),
    ("/apply-implement/", STAGE_APPLY_IMPLEMENT),
    ("/apply-green/", STAGE_APPLY_GREEN),
)

_STAGE_JSON_SUFFIX = {
    STAGE_APPLY_SCAFFOLD: "SCAFFOLD.json",
    STAGE_APPLY_RED: "RED.json",
    STAGE_APPLY_IMPLEMENT: "IMPLEMENTATION.json",
    STAGE_APPLY_GREEN: "GREEN.json",
}


def slice_receipt_path_from_edit(
    tool_name: str,
    args: Mapping[str, Any],
    *,
    repo_root: Path,
) -> tuple[Path, str] | None:
    """Вернуть (абсолютный путь, stage) если edit затрагивает slice receipt JSON."""
    if str(tool_name or "").strip().lower() != "edit":
        return None
    repo = resolve_opsx_repository_root(repo_root)
    for raw in paths_from_tool_args(tool_name, args):
        rel = to_repo_relative(raw, repo)
        normalized = rel.replace("\\", "/")
        if not normalized.endswith(".json"):
            continue
        for marker, stage in _STAGE_BY_MARKER:
            if marker not in f"/{normalized}":
                continue
            if not normalized.endswith(f"/{_STAGE_JSON_SUFFIX[stage]}"):
                continue
            return (resource_path(repo, normalized)).resolve(), stage
    return None


def _validate_stage_receipt(path: Path, stage: str, *, repo_root: Path) -> Any:
    if stage == STAGE_APPLY_SCAFFOLD:
        return validate_scaffold_operative_projected(path, repo_root=repo_root)
    if stage == STAGE_APPLY_RED:
        return validate_red_operative(path, repo_root=repo_root)
    if stage == STAGE_APPLY_IMPLEMENT:
        return validate_implement_operative(path, repo_root=repo_root)
    if stage == STAGE_APPLY_GREEN:
        return validate_green_operative(path, repo_root=repo_root)
    raise ValueError(f"unknown stage {stage}")


def _is_edit_started(record: Mapping[str, Any]) -> bool:
    if record.get("kind") == "interaction" and record.get("update_type") == "tool-call-started":
        return True
    if record.get("kind") == "tool_call" and record.get("status") == "running":
        return True
    return False


def _is_edit_completed(record: Mapping[str, Any]) -> bool:
    if record.get("kind") == "interaction" and record.get("update_type") == "tool-call-completed":
        return True
    if record.get("kind") == "tool_call" and str(record.get("status") or "").lower() == "completed":
        return True
    return False


def enforce_slice_receipt_after_edit(
    tool_name: str,
    args: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    repo_root: Path,
    snapshots: MutableMapping[str, str],
) -> None:
    """Снимок до edit и schema gate после completed; при FAIL — откат и ``ReadScopeViolation``."""
    hit = slice_receipt_path_from_edit(tool_name, args, repo_root=repo_root)
    if hit is None:
        return
    receipt_path, stage = hit
    key = receipt_path.as_posix()
    if _is_edit_started(record):
        if receipt_path.is_file():
            snapshots[key] = receipt_path.read_text(encoding="utf-8")
        else:
            snapshots.pop(key, None)
        return
    if not _is_edit_completed(record):
        return
    if not receipt_path.is_file():
        raise ReadScopeViolation(
            tool=tool_name,
            path=key,
            rule=SLICE_RECEIPT_SCHEMA_RULE,
            command="stage receipt JSON missing after edit",
        )
    gate = _validate_stage_receipt(receipt_path, stage, repo_root=repo_root)
    if gate.ok:
        snapshots.pop(key, None)
        return
    prior = snapshots.pop(key, None)
    if prior is not None:
        _ = receipt_path.write_text(prior, encoding="utf-8")
    elif receipt_path.is_file():
        receipt_path.unlink()
    detail = format_scaffold_gate_errors_for_model(list(gate.errors))
    raise ReadScopeViolation(
        tool=tool_name,
        path=key,
        rule=SLICE_RECEIPT_SCHEMA_RULE,
        command=detail[:4000],
    )


def slice_receipt_write_correction_prompt(
    violation: ReadScopeViolation,
    *,
    base_prompt: str,
) -> str:
    """Дописать в hop-промпт текст после отклонённого hand-write receipt edit."""
    errors = format_scaffold_gate_errors_for_model(
        str(violation.command or violation).strip(),
    )
    tool_block = slice_receipt_hop_tool_block()
    correction = (
        "## Stage receipt WRITE REJECTED (machine schema)\n\n"
        "Your last direct edit to a slice stage receipt JSON was rolled back. "
        "Do not edit slice receipt JSON by hand; the runtime records it after hop completion.\n\n"
        f"Validator errors:\n{errors}\n\n"
        f"{tool_block}"
    )
    base = (base_prompt or "").strip()
    if not base:
        return correction
    return f"{base}\n\n{correction}"
