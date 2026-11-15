"""DI factory для ``AgentToolRegistry``."""

from __future__ import annotations

from dependency_injector import containers, providers

from agents.adapters.abc_adapter import ABCAdapter
from agents.agent_tools.registry import AgentToolRegistry


class AgentToolsContainer(containers.DeclarativeContainer):
    """Registry на том же mutating adapter, что parent hops."""

    adapter = providers.Dependency(instance_of=ABCAdapter)

    registry = providers.Factory(
        AgentToolRegistry,
        adapter=adapter,
        allow_deterministic_fallback=providers.Object(False),
    )
