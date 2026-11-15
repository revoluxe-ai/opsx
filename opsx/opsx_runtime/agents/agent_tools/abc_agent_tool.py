"""Базовый класс вложенного агента: ``invoke`` вместо ``run(GraphState)``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel

from agents.abc_agent import ABCAgent
from agents.adapters.abc_adapter import ABCAdapter
from agents.agent_tools.parent_context import AgentToolParentContext

_In = TypeVar("_In", bound=BaseModel)
_Out = TypeVar("_Out", bound=BaseModel)


class ABCAgentTool(ABCAgent, ABC, Generic[_In, _Out]):
    """Суб-hop агента: вызывается из custom tool handler parent hop."""

    role: ClassVar[str] = "agent_tool"
    tools: ClassVar[tuple[str, ...]] = ("mcp",)
    payload_model: ClassVar[type[BaseModel] | None] = None

    _child_agent_key: str
    _child_adapter: ABCAdapter

    def __init__(self, adapter: ABCAdapter) -> None:
        super().__init__(adapter)
        self._child_adapter = adapter
        self._child_agent_key = self.role

    @abstractmethod
    async def invoke(
        self,
        parent_ctx: AgentToolParentContext,
        hop_input: _In,
        *,
        model: dict[str, Any] | None = None,
        workspace: str | None = None,
    ) -> _Out:
        """Выполнить изолированный child hop (``resume_agent_id=None``)."""
