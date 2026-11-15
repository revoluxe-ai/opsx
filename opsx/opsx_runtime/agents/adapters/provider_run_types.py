"""Neutral execution result types for adapter stream tests."""

from __future__ import annotations

from typing import Any

# Structural typing only outside the provider package; provider tests use concrete types.
RunResult = Any
RunStreamEvent = Any

__all__ = ["RunResult", "RunStreamEvent"]
