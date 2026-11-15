"""Shared runtime gates for Python pipeline adapters (projectization + adoption)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from tools.openspec.opsx_mutation_authority import enforce_mutation_authority
from tools.openspec.opsx_mutation_preflight import _projectization_allows


def adoption_block_for_pipeline(
    repo_root: Path,
    entrypoint_id: str,
    *,
    blocked_factory: Callable[..., dict[str, object]],
) -> dict[str, object] | None:
    """Adoption gate после projectization (pipeline-specific blocked shape)."""

    auth = enforce_mutation_authority(repo_root, entrypoint_id)
    if auth.ok:
        return None
    return blocked_factory(
        auth.reason,
        NEXT_ACTION=auth.next_action,
        DETAIL=auth.detail,
        ENTRYPOINT_ID=auth.entrypoint_id,
    )


def projectization_and_adoption_block(
    repo_root: Path,
    entrypoint_id: str,
    *,
    blocked_factory: Callable[..., dict[str, object]],
) -> dict[str, object] | None:
    """Projectization, затем engineering adoption."""

    proj = _projectization_allows(repo_root)
    if proj is not None:
        return blocked_factory(proj.reason, NEXT_ACTION=proj.next_action, DETAIL=proj.detail)
    return adoption_block_for_pipeline(repo_root, entrypoint_id, blocked_factory=blocked_factory)
