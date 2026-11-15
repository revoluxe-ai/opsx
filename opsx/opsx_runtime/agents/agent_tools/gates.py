"""Slice gate: обязательные image_tasks из design."""

from __future__ import annotations

from pathlib import Path

from agents.agent_tools.image.acceptance import load_prior_result
from agents.agent_tools.image.resolver import ImageTaskResolver, ImageTaskResolverError


def gate_slice_image_tasks_complete(
    *,
    repo_root: Path,
    change_id: str,
    slice_id: str,
) -> dict[str, object]:
    """BLOCK если в slice есть tasks без ACCEPTED IMAGE-RESULT."""
    resolver = ImageTaskResolver(repo_root=repo_root)
    try:
        tasks = resolver.list_tasks(change_id, slice_id=slice_id)
    except ImageTaskResolverError as exc:
        return {"ok": True, "skipped": True, "reason": exc.code}
    if not tasks:
        return {"ok": True, "skipped": True, "reason": "no_image_tasks"}
    missing: list[str] = []
    for task in tasks:
        prior = load_prior_result(repo_root, change_id, task.task_id)
        if not prior or str(prior.get("status")) != "ACCEPTED":
            missing.append(task.task_id)
    return {
        "ok": not missing,
        "missing_tasks": missing,
        "slice_id": slice_id,
    }
