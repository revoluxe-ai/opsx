"""Parent MCP ``execute_image_task``."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.adapters.agent_capability_options import AgentCustomToolSpec
from typing import TYPE_CHECKING

from agents.agent_tools.image.image_asset_agent import ImageAssetAgent
from agents.agent_tools.image.pipeline import ImageTaskPipeline
from agents.agent_tools.parent_context import AgentToolParentContext

if TYPE_CHECKING:
    from agents.agent_tools.registry import AgentToolRegistry

_TOOL_NAME = "execute_image_task"

_EXECUTE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["task_id"],
    "properties": {
        "task_id": {"type": "string", "minLength": 1},
        "mode": {"type": "string", "enum": ["create", "repair"]},
        "visual_findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
            },
        },
    },
}


def hop_includes_execute_image_task(agent_key: str | None) -> bool:
    """Parent hops с image tool."""
    key = str(agent_key or "").strip()
    return key in ("apply", "apply_implement")


class ExecuteImageTaskRuntime:
    """Handler parent tool → pipeline."""

    def __init__(
        self,
        *,
        image_agent: ImageAssetAgent,
        parent_ctx: AgentToolParentContext,
        allow_deterministic_fallback: bool = False,
    ) -> None:
        self._parent_ctx = parent_ctx
        self._pipeline = ImageTaskPipeline(
            image_agent=image_agent,
            allow_deterministic_fallback=allow_deterministic_fallback,
        )

    async def execute_image_task(self, args: Mapping[str, Any], _ctx: Any) -> dict[str, Any]:
        if not isinstance(args, Mapping):
            body = {"ok": False, "code": "INVALID_ARGS"}
            return {"content": [{"type": "text", "text": json.dumps(body)}], **body}
        task_id = str(args.get("task_id") or "").strip()
        mode = str(args.get("mode") or "create").strip()
        findings = args.get("visual_findings")
        vf = findings if isinstance(findings, list) else None
        result = await self._pipeline.execute(
            self._parent_ctx,
            task_id=task_id,
            mode=mode,
            visual_findings=vf,
        )
        text = result.model_dump_json(indent=2)
        payload = result.model_dump(mode="json")
        return {"content": [{"type": "text", "text": text}], "ok": result.status != "BLOCKED", **payload}


def build_execute_image_task_tool(
    *,
    image_agent: ImageAssetAgent,
    parent_ctx: AgentToolParentContext,
    allow_deterministic_fallback: bool = False,
) -> dict[str, AgentCustomToolSpec]:
    runtime = ExecuteImageTaskRuntime(
        image_agent=image_agent,
        parent_ctx=parent_ctx,
        allow_deterministic_fallback=allow_deterministic_fallback,
    )
    return {
        _TOOL_NAME: AgentCustomToolSpec(
            description=(
                "Run ImageAssetAgent for one design image_task: LLM chooses "
                "compose/generate, then technical + visual gates."
            ),
            input_schema=_EXECUTE_SCHEMA,
            execute=runtime.execute_image_task,
        ),
    }
