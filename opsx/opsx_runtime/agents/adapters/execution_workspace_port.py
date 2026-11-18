"""Provider-owned repository execution workspace, not a second workflow."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Protocol, Any, runtime_checkable

_ALLOWED = frozenset({
    "agents.adapters.cursor_sdk",
    "agents.adapters.codex_app_server",
})


@runtime_checkable
class ExecutionWorkspace(Protocol):
    async def __aenter__(self) -> Path: ...
    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any: ...


def open_execution_workspace(
    source: Path, *, run_id: str, provider_module: str | None = None,
) -> ExecutionWorkspace:
    selected = provider_module or os.environ.get("OPSX_ADAPTER_MODULE") or "agents.adapters.cursor_sdk"
    if selected not in _ALLOWED:
        raise RuntimeError("EXECUTION_PROVIDER_UNAUTHORIZED")
    importlib.import_module(selected)
    from agents.adapters.provider_registry import build_execution_workspace
    workspace = build_execution_workspace(
        selected, source=Path(source).resolve(), run_id=run_id,
    )
    if not isinstance(workspace, ExecutionWorkspace):
        raise TypeError("EXECUTION_WORKSPACE_CONTRACT_INVALID")
    return workspace
