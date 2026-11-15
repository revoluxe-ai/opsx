"""Контекст durable SQLite store адаптера для scope ``verify_impl:{change_id}``.

Связывает composition root ``run_verify_impl`` (``store_backend=sqlite``,
``store_scope=verify_impl:<id>``) с путём ``index.db`` через ``bridge_local``.
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import sys
from dataclasses import dataclass
from pathlib import Path

_RUNTIME_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = project_root()
_EXECUTOR_ROOT = resource_path(_REPO_ROOT, "tools", "cursor_executor")
if str(_EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXECUTOR_ROOT))

from cursor_executor import bridge_local as _bridge_local  # noqa: E402

bridge_launch_kwargs = _bridge_local.bridge_launch_kwargs
_adapter_sqlite_store_root = _bridge_local.adapter_sqlite_store_root

from workflows.repo_paths import resolve_opsx_repository_root  # noqa: E402

VERIFY_IMPL_STORE_BACKEND = "sqlite"


def verify_impl_store_scope(change_id: str) -> str:
    """Стабильный recovery scope parent verify-impl, как у ``CursorBridgeRuntime``."""
    cid = str(change_id).strip()
    if not cid:
        raise ValueError("VERIFY_IMPL_CHANGE_ID_REQUIRED")
    return f"verify_impl:{cid}"


@dataclass(frozen=True, slots=True)
class VerifyImplAdapterStoreContext:
    """Разрешённые пути store для одного ``change_id`` verify-impl."""

    change_id: str
    repo_root: Path
    store_backend: str
    store_scope: str
    sqlite_root: Path
    index_db: Path

    def bridge_launch_kwargs(self) -> dict:
        """Kwargs для ``AsyncClient.launch_bridge`` / ``CursorBridgeRuntime``."""
        return bridge_launch_kwargs(
            agent_cwd=str(self.repo_root),
            store_backend=self.store_backend,
            store_scope=self.store_scope,
        )


def resolve_verify_impl_adapter_store(
    change_id: str,
    *,
    repo_root: Path,
) -> VerifyImplAdapterStoreContext:
    """Разрешить sqlite root и ``index.db`` для reconcile без открытия bridge.

    Args:
        change_id: OpenSpec change id.
        repo_root: Корень репозитория OPSX (с ``opsx``).

    Returns:
        Контекст с путём к ``index.db`` (файл может ещё не существовать).
    """
    root = resolve_opsx_repository_root(repo_root)
    scope = verify_impl_store_scope(change_id)
    sqlite_root = _adapter_sqlite_store_root(root, scope)
    return VerifyImplAdapterStoreContext(
        change_id=str(change_id).strip(),
        repo_root=root,
        store_backend=VERIFY_IMPL_STORE_BACKEND,
        store_scope=scope,
        sqlite_root=sqlite_root,
        index_db=sqlite_root / "index.db",
    )
