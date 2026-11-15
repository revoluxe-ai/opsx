"""Дополнение restrict_allow work-путями slice hops (на общей политике для всех агентов)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from agents.config import ReadScope
from workflows.implementation.routing_contract import (
    STAGE_APPLY_GREEN,
    STAGE_APPLY_SCAFFOLD,
    STAGE_APPLY_RED,
    STAGE_APPLY_IMPLEMENT,
)
from workflows.implementation.slice_check_workflow_receipt import evaluate_slice_stage
from workflows.implementation.slice_receipt import (
    green_work_paths,
    load_stage_receipt,
    normalize_work_path,
    work_paths_from_receipt,
)
from workflows.repo_paths import resolve_opsx_repository_root

_SLICE_HOP_AGENT_KEYS = frozenset(
    {
        "apply_scaffold",
        "apply_red",
        "apply_implement",
        "apply_green",
    }
)

_IMPL_VERIFY_AGENT_KEYS = frozenset({"impl_verify"})


def is_slice_hop_agent_key(agent_key: str | None) -> bool:
    """Агент относится к slice pipeline (может расширить allow work-путями)."""
    return str(agent_key or "").strip() in _SLICE_HOP_AGENT_KEYS


def _work_paths_for_hop(
    change_id: str,
    slice_id: str,
    stage: str,
    *,
    repo_root: Path,
) -> tuple[str, ...]:
    """Собрать work scope: стадии slice + receipt + evaluate_slice_stage."""
    collected: list[str] = []
    for st in (
        STAGE_APPLY_SCAFFOLD,
        STAGE_APPLY_RED,
        STAGE_APPLY_IMPLEMENT,
        STAGE_APPLY_GREEN,
    ):
        if st == STAGE_APPLY_GREEN and stage != STAGE_APPLY_GREEN:
            continue
        receipt = load_stage_receipt(change_id, slice_id, st, repo_root=repo_root)
        if isinstance(receipt, dict):
            try:
                if st == STAGE_APPLY_GREEN:
                    collected.extend(
                        green_work_paths(change_id, slice_id, repo_root=repo_root)
                    )
                else:
                    collected.extend(work_paths_from_receipt(st, receipt))
            except ValueError:
                pass
    try:
        eval_impl = evaluate_slice_stage(
            change_id,
            slice_id,
            stage,
            repo_root=repo_root,
            state={},
        )
        collected.extend(eval_impl.work_paths)
    except (OSError, ValueError, TypeError):
        pass
    normalized = sorted(
        {p for raw in collected if (p := normalize_work_path(str(raw)))}
    )
    return tuple(normalized)


def augment_scope_with_slice_work_paths(
    scope: ReadScope,
    state: Mapping[str, Any],
    *,
    agent_key: str | None,
) -> ReadScope:
    """Union work paths в restrict_allow; не ослабляет глобальную политику."""
    key = str(agent_key or "").strip()
    if not is_slice_hop_agent_key(key):
        return scope
    change_id = str(state.get("change_id") or "").strip()
    slice_id = str(state.get("implementation_slice_id") or "").strip()
    if not change_id or not slice_id:
        return scope
    stage = str(
        state.get("implementation_stage")
        or key.replace("apply_", "APPLY_").upper()
    )
    root = resolve_opsx_repository_root(
        Path(str(state.get("repository_path") or ".")),
    )
    work = _work_paths_for_hop(change_id, slice_id, stage, repo_root=root)
    work_parent_globs: list[str] = []
    for wp in work:
        if "/" in wp:
            work_parent_globs.append(f"{wp.rsplit('/', 1)[0]}/**")
    base = scope.restrict_allow or ()
    merged = tuple(sorted(set(base) | set(work) | set(work_parent_globs)))
    return ReadScope(
        allow=scope.allow,
        deny=scope.deny,
        restrict_allow=merged,
        restrict_write_allow=scope.restrict_write_allow,
    )


def declared_product_paths_from_apply_design(
    change_id: str,
    *,
    repo_root: Path,
) -> tuple[str, ...]:
    """Явные product-пути из ``APPLY-DESIGN.json`` (все slices), без скана репозитория."""
    from workflows.artifact_paths import apply_design_artifact_path

    design_path = apply_design_artifact_path(repo_root, change_id)
    if not design_path.is_file():
        return ()
    try:
        design = json.loads(design_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return ()
    slices = design.get("slices") if isinstance(design, dict) else None
    if not isinstance(slices, list):
        return ()
    collected: list[str] = []
    for row in slices:
        if not isinstance(row, dict):
            continue
        for key in ("expected_product_write_surfaces", "relevant_existing_surfaces"):
            raw = row.get(key)
            if not isinstance(raw, list):
                continue
            for item in raw:
                if isinstance(item, str) and item.strip():
                    norm = normalize_work_path(item.strip())
                    if norm:
                        collected.append(norm)
    return tuple(sorted(set(collected)))


def _merge_explicit_paths_into_scope(
    scope: ReadScope,
    paths: tuple[str, ...],
) -> ReadScope:
    """Union явных repo-relative путей (как slice work_paths), без расширения на repo root."""
    if not paths:
        return scope
    work_parent_globs: list[str] = []
    for wp in paths:
        if "/" in wp:
            work_parent_globs.append(f"{wp.rsplit('/', 1)[0]}/**")
    base = scope.restrict_allow or ()
    merged = tuple(sorted(set(base) | set(paths) | set(work_parent_globs)))
    return ReadScope(
        allow=scope.allow,
        deny=scope.deny,
        restrict_allow=merged,
        restrict_write_allow=scope.restrict_write_allow,
    )


def augment_scope_with_change_declared_paths(
    scope: ReadScope,
    state: Mapping[str, Any],
    *,
    agent_key: str | None,
) -> ReadScope:
    """Для ``impl_verify``: planning paths + APPLY-DESIGN product surfaces (work_paths parity)."""
    key = str(agent_key or "").strip()
    if key not in _IMPL_VERIFY_AGENT_KEYS:
        return scope
    change_id = str(state.get("change_id") or "").strip()
    if not change_id:
        return scope
    root = resolve_opsx_repository_root(
        Path(str(state.get("repository_path") or ".")),
    )
    from agents.verify.impl_verify.preflight import (
        derive_referenced_repo_paths,
        load_change_artifact_bundle,
    )

    bundle = load_change_artifact_bundle(change_id, repo_root=root)
    referenced = tuple(
        normalize_work_path(p)
        for p in derive_referenced_repo_paths(change_id, bundle, repo_root=root)
        if normalize_work_path(p)
    )
    declared = declared_product_paths_from_apply_design(change_id, repo_root=root)
    work = tuple(sorted(set(referenced) | set(declared)))
    return _merge_explicit_paths_into_scope(scope, work)
