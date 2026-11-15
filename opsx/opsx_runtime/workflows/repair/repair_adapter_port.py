"""Workflow repair adapter port (model selection + factory via neutral modules)."""

from __future__ import annotations

from agents.adapters.hop_model_selection import (
    default_hop_model_selection,
    model_selection_for_hop,
)
from typing import Any

from agents.adapters.abc_adapter import ABCAdapter
from agents.adapters.provider_registry import build_repair_adapter
from agents.adapters.repair_tool_policy import tools_for_adapter_create


class RepairAdapter:
    """Callable surface for mutating adapter construction (provider registry)."""

    def __new__(cls, **kwargs: Any) -> ABCAdapter:
        return build_repair_adapter(**kwargs)


__all__ = [
    "RepairAdapter",
    "build_repair_adapter",
    "default_hop_model_selection",
    "model_selection_for_hop",
    "tools_for_adapter_create",
]
