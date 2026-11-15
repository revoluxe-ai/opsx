"""ImageAssetAgent: LLM-hop с child tools (не прямой Pillow из parent tool)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agents.adapters.abc_adapter import ABCAdapter, schema_format_reminder
from agents.abc_agent import ABCAgent
from agents.agent_tools.abc_agent_tool import ABCAgentTool
from agents.agent_tools.image.child_tools import (
    ImageAssetChildToolRuntime,
    build_image_child_custom_tools,
)
from agents.agent_tools.image.ports.cursor_generation import CursorProviderImageGenerationPort
from agents.agent_tools.image.ports.model_input import ImageModelInputPort
from agents.agent_tools.image.ports.render import ImageRenderPort
from agents.agent_tools.image.schemas import ImageAssetHopInput
from agents.agent_tools.parent_context import AgentToolParentContext


class ImageAssetHopAnswer(BaseModel):
    """Ответ child hop после вызова compose/generate tools."""

    status: str = Field(description="DONE|BLOCKED")
    chosen_operation: str = ""
    notes: str = ""


class ImageAssetAgent(ABCAgentTool[ImageAssetHopInput, ImageAssetHopAnswer]):
    """ИИ-hop: модель читает refs и вызывает image_compose / image_generate."""

    role = "image_asset"
    payload_model = ImageAssetHopAnswer
    tools = ("mcp",)

    def __init__(self, adapter: ABCAdapter) -> None:
        super().__init__(adapter)
        self._child_agent_key = "image_asset"

    def _build_prompt(self, hop_input: ImageAssetHopInput, parent_ctx: AgentToolParentContext) -> str:
        task = hop_input.task
        findings = hop_input.visual_findings
        repair_block = ""
        if hop_input.mode.value == "repair" and findings:
            lines = "\n".join(f"- {f.code}: {f.message}" for f in findings)
            repair_block = f"\n## Visual repair findings\n{lines}\nYou MUST change output bytes.\n"
        return (
            f"You are ImageAssetAgent for change {parent_ctx.change_id}.\n"
            f"Task {task.task_id}: {task.purpose}\n"
            f"Output path (allowlisted): {task.output_path}\n"
            f"Target IHDR: {task.acceptance.ihdr}\n"
            f"Copy lines: {task.copy_text}\n"
            f"Style refs: {task.style_refs}\n"
            f"Generation mode: {task.generation_mode}\n"
            f"{repair_block}"
            "Use read_image_page to inspect refs/output, then call image_compose "
            "or image_generate once to write the file.\n"
        )

    async def invoke(
        self,
        parent_ctx: AgentToolParentContext,
        hop_input: ImageAssetHopInput,
        *,
        model: dict[str, Any] | None = None,
        workspace: str | None = None,
    ) -> ImageAssetHopAnswer:
        repo = Path(parent_ctx.repository_path).resolve()
        gen = CursorProviderImageGenerationPort(allow_compose_fallback=True)
        model_input = ImageModelInputPort(repo_root=repo)
        child_runtime = ImageAssetChildToolRuntime(
            repo_root=repo,
            task=hop_input.task,
            generation=gen,
            model_input=model_input,
            render=ImageRenderPort(repo_root=repo),
        )
        custom_tools = build_image_child_custom_tools(child_runtime)
        transport: dict[str, Any] = {
            "repository_path": str(repo),
            "workspace": workspace or str(repo),
            "model": model or {"id": "composer-2.5"},
            "image_child_custom_tools": custom_tools,
        }
        prompt = self._build_prompt(hop_input, parent_ctx)
        outcome = await ABCAgent.execute_validated(
            self._child_adapter,
            transport,
            prompt=prompt,
            tools=("mcp",),
            resume_agent_id=None,
            payload_model=ImageAssetHopAnswer,
            format_instructions=schema_format_reminder(ImageAssetHopAnswer),
            agent_key=self._child_agent_key,
        )
        if outcome.parsed is None:
            return ImageAssetHopAnswer(status="BLOCKED", notes="no parsed payload")
        return ImageAssetHopAnswer.model_validate(outcome.parsed.model_dump(mode="json"))
