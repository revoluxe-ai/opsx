"""Shared fail-closed preflight for OPSX mutation-capable commands."""

from __future__ import annotations

from opsx.project_context import resource_path

from dataclasses import dataclass
from pathlib import Path

import yaml

from tools.openspec.opsx_engineering_adoption_gate import engineering_adoption_allows_mutations

PROJECTIZATION_REL = Path("opsx/projectization.yaml")


@dataclass(frozen=True)
class MutationPreflightResult:
    """Итог projectization + engineering adoption для mutation path."""

    ok: bool
    reason: str
    detail: str = ""
    next_action: str = ""


def _projectization_allows(root: Path) -> MutationPreflightResult | None:
    path = resource_path(root, PROJECTIZATION_REL)
    if not path.is_file():
        return MutationPreflightResult(
            ok=False,
            reason="OPSX_PROJECTIZATION_REQUIRED",
            detail="missing projectization.yaml",
            next_action="/opsx-projectize",
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return MutationPreflightResult(
            ok=False,
            reason="OPSX_PROJECTIZATION_REQUIRED",
            detail="invalid projectization.yaml",
            next_action="/opsx-projectize",
        )
    if str(data.get("status", "")) != "COMPLETE" or data.get("normal_commands_enabled") is not True:
        return MutationPreflightResult(
            ok=False,
            reason="OPSX_PROJECTIZATION_REQUIRED",
            detail=f"status={data.get('status')!r}",
            next_action="/opsx-projectize",
        )
    return None


def evaluate_opsx_mutation_preflight(
    root: Path,
    *,
    command_id: str | None = None,
) -> MutationPreflightResult:
    """Projectization first, then engineering adoption (bootstrap allowlist)."""

    proj_block = _projectization_allows(root)
    if proj_block is not None:
        return proj_block
    adoption = engineering_adoption_allows_mutations(root, command_id=command_id)
    if not adoption.ok:
        return MutationPreflightResult(
            ok=False,
            reason=adoption.reason,
            detail=adoption.detail,
            next_action=adoption.next_action,
        )
    return MutationPreflightResult(ok=True, reason="OK")
