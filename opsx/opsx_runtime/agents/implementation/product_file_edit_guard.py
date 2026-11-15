"""Откат разрушительных edit на product/test файлах во slice hops."""

from __future__ import annotations

from opsx.project_context import resource_path

from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any

from agents.config import ReadScopeViolation, paths_from_tool_args, to_repo_relative
from workflows.repo_paths import resolve_opsx_repository_root

PRODUCT_EDIT_DESTRUCTIVE_RULE = "product_edit_destructive"

# Минимальный размер файла, для которого включён fail-closed откат.
_MIN_PRIOR_BYTES = 400
# Пустой файл или потеря >80% байт после крупного исходника.
_SHRINK_RATIO = 0.20


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


def _guarded_product_rel_paths(
    tool_name: str,
    args: Mapping[str, Any],
    *,
    repo_root: Path,
) -> list[str]:
    """Repo-relative пути под ``src/`` или ``tests/``, не openspec."""
    if str(tool_name or "").strip().lower() != "edit":
        return []
    repo = resolve_opsx_repository_root(repo_root)
    out: list[str] = []
    for raw in paths_from_tool_args(tool_name, args):
        rel = to_repo_relative(raw, repo).replace("\\", "/")
        if not rel or rel.startswith("openspec/"):
            continue
        if (
            rel.startswith("src/")
            or rel.startswith("tests/")
            or rel.startswith("migrations/versions/")
        ):
            out.append(rel)
    return out


def _destructive_shrink(prior: str, after: str) -> bool:
    prior_b = prior.encode("utf-8")
    after_b = after.encode("utf-8")
    if len(prior_b) < _MIN_PRIOR_BYTES:
        return False
    if not after_b.strip():
        return True
    if len(after_b) <= int(len(prior_b) * _SHRINK_RATIO):
        return True
    return False


def enforce_product_file_edit_guard(
    tool_name: str,
    args: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    repo_root: Path,
    snapshots: MutableMapping[str, str],
) -> None:
    """Снимок до edit; после completed — откат и violation при обнулении/массовом сносе."""
    rel_paths = _guarded_product_rel_paths(tool_name, args, repo_root=repo_root)
    if not rel_paths:
        return
    repo = resolve_opsx_repository_root(repo_root)
    if _is_edit_started(record):
        for rel in rel_paths:
            path = (resource_path(repo, rel)).resolve()
            key = path.as_posix()
            if path.is_file():
                snapshots[key] = path.read_text(encoding="utf-8")
            else:
                snapshots.pop(key, None)
        return
    if not _is_edit_completed(record):
        return
    for rel in rel_paths:
        path = (resource_path(repo, rel)).resolve()
        key = path.as_posix()
        prior = snapshots.pop(key, None)
        if prior is None:
            continue
        after = path.read_text(encoding="utf-8") if path.is_file() else ""
        if not _destructive_shrink(prior, after):
            continue
        _ = path.write_text(prior, encoding="utf-8")
        raise ReadScopeViolation(
            tool=tool_name,
            path=rel,
            rule=PRODUCT_EDIT_DESTRUCTIVE_RULE,
            command=(
                "edit rolled back: file shrank to empty or lost >80% of prior content "
                f"({len(prior.encode('utf-8'))} bytes before). Use minimal hunks only."
            ),
        )


def product_edit_destructive_correction_prompt(
    violation: ReadScopeViolation,
    *,
    base_prompt: str,
) -> str:
    """Дописать промпт после отката разрушительного edit."""
    correction = (
        "## PRODUCT EDIT REJECTED (destructive)\n\n"
        "Your last edit was rolled back: it emptied or deleted most of a large "
        "`src/**` or `tests/**` file. Do **not** replace whole files.\n"
        "Use a minimal hunk on the exact failing seam, or set status=BLOCKED with "
        "`remaining_gaps` if you lack authorized scope.\n\n"
        f"Detail: {str(violation.command or violation).strip()}\n"
    )
    base = (base_prompt or "").strip()
    return f"{base}\n\n{correction}" if base else correction