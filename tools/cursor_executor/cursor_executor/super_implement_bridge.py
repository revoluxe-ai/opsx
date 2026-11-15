"""Build CursorExecutionRequest payloads for Super Implement primitive dispatch."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .command_preflight import validate_command_invocation
from .schemas import CursorExecutionRequest, ModelRoute, RuntimeKind, WritePolicy


def _inherit_model_route() -> ModelRoute:
    raw = os.environ.get("SUPER_IMPLEMENT_RESOLVED_MODEL_ROUTE_JSON")
    if raw:
        data = json.loads(raw)
        return ModelRoute.from_json(data)
    return ModelRoute(
        model_id="gpt-5.6-sol",
        params={"context": "272k", "reasoning": "high", "fast": "false"},
    )


def build_super_implement_request(payload: Mapping[str, Any]) -> CursorExecutionRequest:
    """Materialize executor request from Super Implement dispatch spec JSON."""

    cursor = payload.get("cursor_request") or payload
    prompt = str(cursor.get("prompt") or payload.get("prompt") or "")
    if not prompt.startswith("/"):
        raise ValueError("super implement prompt must be a slash command")
    metadata = dict(cursor.get("metadata") or {})
    metadata.setdefault("model_mode", "INHERIT")
    metadata.setdefault("requested_model", "inherit")
    metadata.setdefault("tier_substitution_forbidden", True)
    return CursorExecutionRequest(
        execution_id=str(cursor.get("execution_id") or payload.get("execution_id")),
        correlation_id=cursor.get("correlation_id"),
        workspace=str(cursor.get("workspace") or payload.get("workspace") or "."),
        runtime=RuntimeKind(str(cursor.get("runtime", "LOCAL"))),
        model_route=ModelRoute.from_json(cursor.get("model_route") or _inherit_model_route().to_json()),
        prompt=prompt,
        write_policy=WritePolicy(str(cursor.get("write_policy", "MUTATING"))),
        timeout_seconds=cursor.get("timeout_seconds"),
        metadata=metadata,
    )


def validate_super_implement_dispatch(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Read-only: command contract preflight for a planned primitive (no SDK call)."""

    request = build_super_implement_request(payload)
    block = validate_command_invocation(request)
    command_id = metadata_command_id(request)
    contract_path = Path(request.workspace) / ".cursor" / "commands" / f"{command_id}.md"
    return {
        "ok": block is None,
        "command_id": command_id,
        "contract_exists": contract_path.is_file(),
        "preflight_code": block.code if block else None,
        "model_mode": request.metadata.get("model_mode"),
        "requested_model": request.metadata.get("requested_model"),
        "write_policy": request.write_policy.value,
        "execution_id": request.execution_id,
    }


def metadata_command_id(request: CursorExecutionRequest) -> str:
    if request.metadata.get("command_id"):
        return str(request.metadata["command_id"])
    prompt = request.prompt.strip()
    if prompt.startswith("/"):
        return prompt.split()[0][1:]
    return ""
