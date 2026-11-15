"""Вложенные агенты-инструменты (META): parent custom MCP → child ABCAgent hop.

Пакет не импортирует провайдер; wiring — через ``AgentToolRegistry`` и DI.
"""

from agents.agent_tools.parent_context import AgentToolParentContext
from agents.agent_tools.registry import AgentToolRegistry

__all__ = ["AgentToolParentContext", "AgentToolRegistry"]
