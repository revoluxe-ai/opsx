# -*- coding: utf-8 -*-
"""Детерминированная сборка ``required_checks`` для bounded verification plan.

Единственный supported composer перед ``draft_plan``. Не выполняет checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from bounded_verify_completion import channel_impact_applicable, split_apply_completion_payload
from bounded_verify_plan import REPO_ROOT, change_dir

OBLIGATIONS_PATH = (
    REPO_ROOT
    / ".cursor"
    / "pipeline"
    / "bounded-verify"
    / "change_verification_obligations.yaml"
)

OPSX_PREFIX = "opsx-"


def _load_obligations_config(*, root: Path = REPO_ROOT) -> dict[str, Any]:
    path = root / ".cursor" / "pipeline" / "bounded-verify" / "change_verification_obligations.yaml"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("obligations config invalid")
    return data


def compose_required_checks(change_id: str, *, root: Path = REPO_ROOT) -> list[dict[str, Any]]:
    """Собрать упорядоченный список ``{check_id, order}`` для change.

    Args:
        change_id: OpenSpec change id.
        root: Repo root.

    Returns:
        Нормализованный список checks для ``draft_plan``.
    """
    cfg = _load_obligations_config(root=root)
    cdir = change_dir(change_id, root=root)
    if not cdir.is_dir():
        raise ValueError(f"change not found: {change_id}")

    check_ids: list[str] = []
    split = split_apply_completion_payload(change_id, root=root)
    is_split = bool(split and split.get("split_apply"))
    pa_channel = channel_impact_applicable(change_id, root=root)

    if is_split:
        check_ids.extend(list(cfg.get("split_apply_prefix", [])))
    if pa_channel:
        check_ids.extend(list(cfg.get("product_acceptance_prefix", [])))
    check_ids.extend(list(cfg.get("static_quality_tail", [])))

    # dedupe preserve order
    seen: set[str] = set()
    ordered_unique: list[str] = []
    for cid in check_ids:
        key = str(cid).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        ordered_unique.append(key)

    return [{"check_id": cid, "order": idx + 1} for idx, cid in enumerate(ordered_unique)]


def compose_and_draft_plan(
    change_id: str,
    *,
    root: Path = REPO_ROOT,
    registry_path: Path | None = None,
):
    """Compose + ``draft_plan`` — supported DRAFT publisher для Owner interface."""
    from bounded_verify_plan import DEFAULT_REGISTRY_PATH, draft_plan  # noqa: PLC0415

    checks = compose_required_checks(change_id, root=root)
    reg = registry_path or DEFAULT_REGISTRY_PATH
    return draft_plan(change_id, checks, root=root, registry_path=reg)
