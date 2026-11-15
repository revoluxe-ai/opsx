"""Resource lifecycle ``CursorBridgeRuntime`` на dependency-injector.

Эквивалент ``AsyncExitStack.enter_async_context`` в ``run_verify_impl.execute``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from dependency_injector import containers, providers

from ..bootstrap import ensure_cursor_sdk_importable
from ..cursor_bridge_runtime import CursorBridgeRuntime

if TYPE_CHECKING:
    pass


async def _cursor_bridge_async_resource(
    repo_root: Path,
    change_id: str,
    *,
    store_backend: str = "sqlite",
) -> AsyncIterator[CursorBridgeRuntime]:
    """Async generator для ``providers.Resource``: enter/exit ``CursorBridgeRuntime``."""
    ensure_cursor_sdk_importable(repo_root=repo_root)
    cid = change_id.strip()
    if not cid:
        raise ValueError("VERIFY_IMPL_CHANGE_ID_REQUIRED")
    store_scope = f"verify_impl:{cid}"
    runtime = CursorBridgeRuntime(
        repo_root.resolve(),
        store_backend=store_backend,
        store_scope=store_scope,
    )
    async with runtime as bridge:
        yield bridge


def _verify_impl_store_scope(change_id: str) -> str:
    """SQLite store scope bridge (per change)."""
    cid = change_id.strip()
    if not cid:
        raise ValueError("VERIFY_IMPL_CHANGE_ID_REQUIRED")
    return f"verify_impl:{cid}"


class BridgeContainer(containers.DeclarativeContainer):
    """Resource-провайдер shared ``CursorBridgeRuntime`` для verify-impl и CLI."""

    config = providers.Configuration()

    repo_root = providers.Object(Path())
    change_id = providers.Object("")
    store_backend = providers.Object("sqlite")

    store_scope = providers.Callable(_verify_impl_store_scope, change_id)

    bridge_runtime = providers.Resource(
        _cursor_bridge_async_resource,
        repo_root=repo_root,
        change_id=change_id,
        store_backend=store_backend,
    )
