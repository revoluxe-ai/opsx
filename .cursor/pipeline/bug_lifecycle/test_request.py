"""Test helpers — hermetic authority is workspace registration, not request fields."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .hermetic_boundary import hermetic_bug_lifecycle_workspace, register_hermetic_workspace


def authorize_workspace(root: Path) -> Path:
    """Register isolated workspace for hermetic bug-lifecycle tests."""
    register_hermetic_workspace(root)
    return root


def with_hermetic_executor(request: dict[str, Any]) -> dict[str, Any]:
    """Return request unchanged; hermetic authority is outside caller-controlled fields."""

    return dict(request)
