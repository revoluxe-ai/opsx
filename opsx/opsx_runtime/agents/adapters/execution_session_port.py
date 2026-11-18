"""Provider-neutral execution lifecycle for OPSX parent orchestration.

The provider owns transport, bridge, reconcile, model-tier and adapter creation.
This port never chooses a different provider if the selected one cannot run.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from agents.adapters.provider_registry import build_execution_session

_DEFAULT_PROVIDER = "agents.adapters.cursor_sdk"
_SUPPORTED = frozenset({
    _DEFAULT_PROVIDER,
    "agents.adapters.codex_app_server",
})


@runtime_checkable
class ExecutionSession(Protocol):
    provider_name: str
    async def __aenter__(self) -> "ExecutionSession": ...
    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any: ...
    async def reconcile(self, change_id: str, workspace: Path, *, execution_id: str) -> Any: ...
    def review_adapter(self, *, timeout_seconds: float) -> Any: ...
    def repair_adapter(self, *, timeout_seconds: float) -> Any: ...
    def model_selection(self) -> dict[str, Any]: ...


def open_execution_session(
    workspace: Path, *, store_backend: str, store_scope: str,
) -> ExecutionSession:
    selected = os.environ.get("OPSX_ADAPTER_MODULE") or _DEFAULT_PROVIDER
    if selected not in _SUPPORTED:
        raise RuntimeError("EXECUTION_PROVIDER_UNAUTHORIZED")
    # Register provider on demand, never fall back to the Cursor SDK.
    importlib.import_module(selected)
    session = build_execution_session(
        selected, workspace=Path(workspace),
        store_backend=store_backend, store_scope=store_scope,
    )
    if not isinstance(session, ExecutionSession):
        raise TypeError("EXECUTION_SESSION_CONTRACT_INVALID")
    return session
