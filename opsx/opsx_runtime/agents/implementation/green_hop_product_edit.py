"""Политика product-edit для apply-green hop (adoption / observation-only)."""

from __future__ import annotations

from pathlib import Path

from workflows.implementation.routing_contract import STAGE_APPLY_IMPLEMENT, STAGE_APPLY_RED
from workflows.implementation.slice_receipt import load_stage_receipt
from workflows.repo_paths import resolve_opsx_repository_root

_ADOPTION_BRANCH = "ADOPTION"
_IMPLEMENTATION_ADOPTED = "IMPLEMENTATION_ADOPTED"
_RED_ALREADY_SATISFIED = "ALREADY_SATISFIED"


def green_hop_allows_product_edit(
    change_id: str,
    slice_id: str,
    *,
    repo_root: Path,
) -> bool:
    """False когда green hop только верифицирует adoption (без product/test writes на implement).

    Случай SLICE-008: RED ``ALREADY_SATISFIED`` + ``observation_only``, implement
    ``IMPLEMENTATION_ADOPTED`` с пустыми ``production_paths_*`` — агент не должен
    вызывать ``edit`` на ``src/**``; red acceptance чинится upstream-срезами или verify.
    """
    if not change_id.strip() or not slice_id.strip():
        return True
    root = resolve_opsx_repository_root(repo_root)
    red = load_stage_receipt(change_id, slice_id, STAGE_APPLY_RED, repo_root=root)
    impl = load_stage_receipt(
        change_id,
        slice_id,
        STAGE_APPLY_IMPLEMENT,
        repo_root=root,
    )
    if not isinstance(red, dict) or not isinstance(impl, dict):
        return True
    if str(impl.get("status") or "") != _IMPLEMENTATION_ADOPTED:
        return True
    if str(impl.get("branch") or "") != _ADOPTION_BRANCH:
        return True
    modified = [p for p in (impl.get("production_paths_modified") or []) if p]
    created = [p for p in (impl.get("production_paths_created") or []) if p]
    if modified or created:
        return True
    if str(red.get("status") or "") != _RED_ALREADY_SATISFIED:
        return True
    if red.get("observation_only") is not True:
        return True
    if red.get("implementation_write_authorized") is True:
        return True
    return False


def green_hop_tools_for_slice(
    base_tools: tuple[str, ...],
    change_id: str,
    slice_id: str,
    *,
    repo_root: Path,
) -> tuple[str, ...]:
    """Убрать ``edit`` из tool list для verify-only adoption green."""
    if green_hop_allows_product_edit(change_id, slice_id, repo_root=repo_root):
        return base_tools
    return tuple(t for t in base_tools if str(t).strip().lower() != "edit")