"""Режим dispatch verify: один finding vs legacy repair unit."""

from __future__ import annotations

from typing import Literal

from agents.state import GraphState

VerifyDispatchMode = Literal["per_finding", "repair_unit"]


def resolve_verify_dispatch_mode(state: GraphState) -> VerifyDispatchMode:
    """Вернуть режим dispatch из state (default ``per_finding``)."""
    raw = state.get("verify_dispatch_mode")
    if isinstance(raw, str) and raw.strip() == "repair_unit":
        return "repair_unit"
    return "per_finding"


__all__ = ["VerifyDispatchMode", "resolve_verify_dispatch_mode"]
