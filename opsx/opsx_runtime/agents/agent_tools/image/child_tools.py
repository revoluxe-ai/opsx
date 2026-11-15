"""Child MCP tools внутри ImageAssetAgent hop."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.adapters.agent_capability_options import AgentCustomToolSpec
from agents.agent_tools.image.ports.model_input import ImageModelInputPort
from agents.agent_tools.image.ports.protocols import ImageGenerationPort, ImageGenerationRequest
from agents.agent_tools.image.ports.render import ImageRenderPort
from agents.agent_tools.image.schemas import ImageDesignTask


def _text_payload(**fields: Any) -> dict[str, Any]:
    body = json.dumps(fields, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": body}], **fields}


class ImageAssetChildToolRuntime:
    """Узкие операции: read / inspect / compose / generate."""

    def __init__(
        self,
        *,
        repo_root: Path,
        task: ImageDesignTask,
        generation: ImageGenerationPort,
        model_input: ImageModelInputPort,
        render: ImageRenderPort,
    ) -> None:
        self._repo = repo_root.resolve()
        self._task = task
        self._generation = generation
        self._model_input = model_input
        self._render = render

    async def read_image_page(self, args: Mapping[str, Any], _ctx: Any) -> dict[str, Any]:
        path = str(args.get("path") or "").strip()
        offset = int(args.get("offset") or 0)
        allowed = {self._task.output_path.replace("\\", "/"), *[
            r.replace("\\", "/") for r in self._task.style_refs
        ]}
        norm = path.replace("\\", "/")
        if norm not in allowed:
            return _text_payload(ok=False, code="PATH_NOT_ALLOWED", path=norm)
        try:
            chunk = self._model_input.page_image(norm, offset=offset)
        except (FileNotFoundError, ValueError) as exc:
            return _text_payload(ok=False, code="READ_FAILED", error=str(exc))
        return _text_payload(
            ok=True,
            path=chunk.path,
            offset=chunk.offset,
            total_bytes=chunk.total_bytes,
            eof=chunk.eof,
            chunk_b64=chunk.chunk_b64,
            mime=chunk.mime,
        )

    async def image_inspect(self, args: Mapping[str, Any], _ctx: Any) -> dict[str, Any]:
        _ = args
        data = await self._render.inspect_bundle()
        return _text_payload(ok=True, operation="inspect", **data)

    async def image_compose(self, args: Mapping[str, Any], _ctx: Any) -> dict[str, Any]:
        hint = str(args.get("prompt_hint") or "")
        req = ImageGenerationRequest(
            task=self._task,
            output_path=(self._repo / self._task.output_path).resolve(),
            repo_root=self._repo,
            prompt_hint=hint,
            use_generative=False,
        )
        result = await self._generation.generate(req)
        return _text_payload(operation="compose", **result)

    async def image_generate(self, args: Mapping[str, Any], _ctx: Any) -> dict[str, Any]:
        hint = str(args.get("prompt_hint") or "")
        req = ImageGenerationRequest(
            task=self._task,
            output_path=(self._repo / self._task.output_path).resolve(),
            repo_root=self._repo,
            prompt_hint=hint,
            use_generative=True,
        )
        result = await self._generation.generate(req)
        return _text_payload(operation="generate", **result)


_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "offset": {"type": "integer", "minimum": 0},
    },
}

_OP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "prompt_hint": {"type": "string"},
    },
}


def build_image_child_custom_tools(
    runtime: ImageAssetChildToolRuntime,
) -> dict[str, AgentCustomToolSpec]:
    """Custom tools для child ImageAssetAgent hop."""
    return {
        "read_image_page": AgentCustomToolSpec(
            description="Read allowlisted PNG in pages (base64 chunks).",
            input_schema=_READ_SCHEMA,
            execute=runtime.read_image_page,
        ),
        "image_inspect": AgentCustomToolSpec(
            description="IHDR/manifest inspect for invest_yalta covers.",
            input_schema={"type": "object", "additionalProperties": False, "properties": {}},
            execute=runtime.image_inspect,
        ),
        "image_compose": AgentCustomToolSpec(
            description="Deterministic compose to task output_path.",
            input_schema=_OP_SCHEMA,
            execute=runtime.image_compose,
        ),
        "image_generate": AgentCustomToolSpec(
            description="Generative/compose lane to task output_path.",
            input_schema=_OP_SCHEMA,
            execute=runtime.image_generate,
        ),
    }
