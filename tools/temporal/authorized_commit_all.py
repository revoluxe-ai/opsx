"""Authorized local executor for post-repair ``/opsx-commit-all GO``.

Review/repair Temporal hops use :class:`~sdk_cursor_adapter.SdkCursorAdapter`
(READ_ONLY SDK tool allowlist). Commit materialization requires a git-capable
hop; reusing the SDK session yields ``STATUS: BLOCKED_NO_SHELL``.

This module is the sole factory for commit-all adapter selection. Tests may
inject a factory via :func:`configure_commit_all_adapter_factory`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from cursor_adapter import CursorAdapter, RealCursorAdapter
from model_config import AgentModelConfig

_COMMIT_ALL_FACTORY: Callable[..., CursorAdapter] | None = None


def configure_commit_all_adapter_factory(
    factory: Callable[..., CursorAdapter] | None,
) -> None:
    """Test hook: replace authorized commit-all adapter construction."""
    global _COMMIT_ALL_FACTORY
    _COMMIT_ALL_FACTORY = factory


def reset_commit_all_adapter_factory() -> None:
    configure_commit_all_adapter_factory(None)


def make_authorized_commit_all_adapter(
    repo_path: str | Path,
    *,
    workflow_key: str,
    model_config: AgentModelConfig | None = None,
) -> CursorAdapter:
    """Return a fresh local CLI adapter authorized for ``/opsx-commit-all GO``.

    Args:
        repo_path: Git workspace root.
        workflow_key: Temporal workflow id fragment for audit correlation.
        model_config: Optional lane model config (composer tier for commit-all).

    Returns:
        CursorAdapter that can execute mutating commit-all prompts.
    """
    if _COMMIT_ALL_FACTORY is not None:
        return _COMMIT_ALL_FACTORY(
            repo_path,
            workflow_key=workflow_key,
            model_config=model_config,
        )
    # Fresh chat per materialize hop — commit-all does not depend on repair chat.
    return RealCursorAdapter(
        repo_path,
        force=True,
        approve_mcps=True,
        model_config=model_config,
    )
