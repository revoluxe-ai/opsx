"""Hermetic execution boundary — not controllable via orchestration request fields."""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_lock = threading.Lock()
_authorized_sandbox: str | None = None
_registered_workspaces: set[str] = set()


class HermeticBoundaryError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _canonical_repo_root() -> Path:
    return REPO_ROOT.resolve()


def _is_repo_descendant(path: Path) -> bool:
    repo = _canonical_repo_root()
    if path == repo:
        return True
    try:
        path.relative_to(repo)
        return True
    except ValueError:
        return False


def _resolve_workspace(path: Path) -> Path:
    try:
        resolved = Path(os.path.realpath(path))
    except OSError as exc:
        raise HermeticBoundaryError("HERMETIC_BOUNDARY_VIOLATION", f"bad workspace: {path}") from exc
    if _is_repo_descendant(resolved):
        raise HermeticBoundaryError(
            "REPO_ROOT_FIXTURE_REFUSED",
            "production repo cannot be registered as hermetic bug-lifecycle workspace",
        )
    return resolved


def authorize_hermetic_sandbox(parent: Path) -> Path:
    resolved = _resolve_workspace(parent)
    with _lock:
        global _authorized_sandbox
        _authorized_sandbox = str(resolved)
    return resolved


def register_hermetic_workspace(root: Path) -> Path:
    resolved = _resolve_workspace(root)
    if _authorized_sandbox is None:
        raise HermeticBoundaryError("SANDBOX_NOT_AUTHORIZED", "hermetic sandbox not authorized")
    sandbox = Path(_authorized_sandbox)
    if resolved != sandbox:
        try:
            resolved.relative_to(sandbox)
        except ValueError:
            raise HermeticBoundaryError(
                "HERMETIC_BOUNDARY_VIOLATION",
                "workspace outside authorized hermetic sandbox",
            )
    with _lock:
        _registered_workspaces.add(str(resolved))
    return resolved


def unregister_hermetic_workspace(root: Path) -> None:
    resolved = _resolve_workspace(root)
    with _lock:
        _registered_workspaces.discard(str(resolved))


def clear_hermetic_authority() -> None:
    with _lock:
        global _authorized_sandbox
        _authorized_sandbox = None
        _registered_workspaces.clear()


def hermetic_execution_enabled(repo_root: Path) -> bool:
    try:
        resolved = _resolve_workspace(repo_root)
    except HermeticBoundaryError:
        return False
    with _lock:
        return str(resolved) in _registered_workspaces


def assert_hermetic_workspace(repo_root: Path) -> Path:
    resolved = _resolve_workspace(repo_root)
    with _lock:
        if str(resolved) not in _registered_workspaces:
            raise HermeticBoundaryError(
                "HERMETIC_BOUNDARY_VIOLATION",
                "workspace not registered for hermetic bug-lifecycle execution",
            )
    return resolved


@contextlib.contextmanager
def hermetic_bug_lifecycle_workspace(root: Path):
    authorize_hermetic_sandbox(root.parent if root.name == "ws" else root)
    register_hermetic_workspace(root)
    try:
        yield root
    finally:
        unregister_hermetic_workspace(root)
        clear_hermetic_authority()
