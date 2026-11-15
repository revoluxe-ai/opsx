"""Neutral adapter bundle typing for composition roots."""

from __future__ import annotations

from dataclasses import dataclass

from agents.adapters.abc_adapter import ABCAdapter


@dataclass(frozen=True)
class AdapterBundle:
    """Review + mutating adapters built by the provider composition root."""

    review_adapter: ABCAdapter
    apply_adapter: ABCAdapter
