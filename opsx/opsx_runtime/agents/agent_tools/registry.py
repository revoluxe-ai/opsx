"""Stateless registry: строит custom tools по ``AgentToolParentContext``."""

from __future__ import annotations

from collections.abc import Mapping

from agents.adapters.agent_capability_options import AgentCustomToolSpec
from agents.adapters.abc_adapter import ABCAdapter
from agents.agent_tools.image.image_asset_agent import ImageAssetAgent
from agents.agent_tools.image.parent_tool import build_execute_image_task_tool
from agents.agent_tools.parent_context import AgentToolParentContext


class AgentToolRegistry:
    """DI-owned; не кладётся в GraphState."""

    def __init__(self, adapter: ABCAdapter, *, allow_deterministic_fallback: bool = False) -> None:
        self._adapter = adapter
        self._allow_deterministic_fallback = allow_deterministic_fallback
        self.image_asset_agent = ImageAssetAgent(adapter)

    def build_custom_tools(
        self,
        parent_ctx: AgentToolParentContext,
    ) -> Mapping[str, AgentCustomToolSpec]:
        return build_execute_image_task_tool(
            image_agent=self.image_asset_agent,
            parent_ctx=parent_ctx,
            allow_deterministic_fallback=self._allow_deterministic_fallback,
        )
