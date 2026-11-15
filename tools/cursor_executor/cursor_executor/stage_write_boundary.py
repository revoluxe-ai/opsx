"""Fail-closed Super Implement stage write boundary metadata for SDK transport."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

from .schemas import CursorExecutionRequest, WritePolicy


@dataclass(frozen=True)
class StageWriteBoundaryBlock:
    code: str
    message: str


def _metadata(request: CursorExecutionRequest) -> Mapping[str, Any]:
    return request.metadata or {}


def validate_super_implement_stage_write_boundary(
    request: CursorExecutionRequest,
) -> StageWriteBoundaryBlock | None:
    """Require explicit permitted_write_globs for Super Implement MUTATING SDK hops."""

    meta = _metadata(request)
    if not meta.get("super_implement"):
        return None
    globs = meta.get("permitted_write_globs")
    if not isinstance(globs, list) or not globs:
        return StageWriteBoundaryBlock(
            code="STAGE_WRITE_BOUNDARY_METADATA_MISSING",
            message="super_implement dispatch requires metadata.permitted_write_globs",
        )
    if request.write_policy == WritePolicy.MUTATING and meta.get("slash_prompt_imitation") is True:
        return StageWriteBoundaryBlock(
            code="STAGE_WRITE_SDK_SLASH_IMITATION_FORBIDDEN",
            message="MUTATING super_implement hops must use native-tool subprocess dispatch",
        )
    enforcement = str(meta.get("stage_write_boundary_enforcement") or "")
    if request.write_policy == WritePolicy.MUTATING:
        if enforcement == "isolated_workspace_v0" and meta.get(
            "super_implement_agent_hop_v0"
        ) is True:
            if os.environ.get("AGENT_HOP_OS_SANDBOX_ENFORCED") != "bwrap":
                return StageWriteBoundaryBlock(
                    code="AGENT_HOP_OS_SANDBOX_ENV_PROOF_MISSING",
                    message=(
                        "MUTATING agent hop SDK requires OS-sandbox-enforced "
                        "subprocess (AGENT_HOP_OS_SANDBOX_ENFORCED=bwrap)"
                    ),
                )
            return None
        if enforcement != "native_subprocess_v1":
            return StageWriteBoundaryBlock(
                code="STAGE_WRITE_BOUNDARY_ENFORCEMENT_REQUIRED",
                message=(
                    "MUTATING super_implement requires "
                    "stage_write_boundary_enforcement=native_subprocess_v1 "
                    "or isolated_workspace_v0 agent hop"
                ),
            )
    return None
