"""Общие типы Cursor SDK адаптеров (colocated с DI)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents.adapters.abc_adapter import ABCAdapter


@dataclass(frozen=True, slots=True)
class CursorSdkAdapterBundle:
    """Пара review + repair адаптеров на одном ``CursorBridgeRuntime``."""

    review_adapter: ABCAdapter
    apply_adapter: ABCAdapter
