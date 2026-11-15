"""Fail-closed проверка on-disk SCAFFOLD.json сразу после edit tool apply-scaffold hop."""

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
from agents.implementation.apply_scaffold.scaffold_receipt_prompt import (
    scaffold_receipt_correction_block,
)
from workflows.implementation.slice_stage_operative_validation import (
    validate_scaffold_operative_projected,
)
from workflows.repo_paths import resolve_opsx_repository_root

SCAFFOLD_RECEIPT_SCHEMA_RULE = "scaffold_receipt_schema"


def scaffold_receipt_path_from_edit(
    tool_name: str,
    args: Mapping[str, Any],
    *,
    repo_root: Path,
) -> Path | None:
    """Вернуть абсолютный путь, если edit затрагивает ``apply-scaffold/*/SCAFFOLD.json``."""
    if str(tool_name or "").strip().lower() != "edit":
        return None
    repo = resolve_opsx_repository_root(repo_root)
    for raw in paths_from_tool_args(tool_name, args):
        rel = to_repo_relative(raw, repo)
        normalized = rel.replace("\\", "/")
        if not normalized.endswith("/SCAFFOLD.json"):
            continue
        if "/apply-scaffold/" not in f"/{normalized}":
            continue
        return (resource_path(repo, normalized)).resolve()
    return None


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


def enforce_scaffold_receipt_after_edit(
    tool_name: str,
    args: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    repo_root: Path,
    snapshots: MutableMapping[str, str],
) -> None:
    """Алиас slice guard (SCAFFOLD-only) для регрессий; rule остаётся ``scaffold_receipt_schema``."""
    from agents.implementation.slice_stage_receipt_edit_guard import (
        enforce_slice_receipt_after_edit,
    )

    try:
        enforce_slice_receipt_after_edit(
            tool_name,
            args,
            record,
            repo_root=repo_root,
            snapshots=snapshots,
        )
    except ReadScopeViolation as exc:
        if exc.rule == "slice_receipt_schema":
            raise ReadScopeViolation(
                tool=exc.tool,
                path=exc.path,
                rule=SCAFFOLD_RECEIPT_SCHEMA_RULE,
                command=exc.command,
                agent_id=exc.agent_id,
                run_id=exc.run_id,
            ) from exc
        raise


def scaffold_receipt_write_correction_prompt(
    violation: ReadScopeViolation,
    *,
    base_prompt: str,
) -> str:
    """Дописать в hop-промпт ошибки schema gate после отклонённого edit SCAFFOLD.json."""
    correction = scaffold_receipt_correction_block(violation)
    base = (base_prompt or "").strip()
    if not base:
        return correction
    return f"{base}\n\n{correction}"


def load_scaffold_receipt_json(path: Path) -> dict[str, Any] | None:
    """Прочитать JSON receipt; ``None`` если файл пуст или не объект."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
