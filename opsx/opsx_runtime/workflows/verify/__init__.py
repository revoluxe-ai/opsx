"""Verify workflows (PHASE_IMPL, archive — по мере добавления нод)."""

from __future__ import annotations

__all__ = ["run_verify_workflow"]


def __getattr__(name: str):
    if name == "run_verify_workflow":
        from .run import run_verify_workflow

        return run_verify_workflow
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
