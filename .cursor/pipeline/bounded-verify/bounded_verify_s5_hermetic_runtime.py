# -*- coding: utf-8 -*-
"""Explicit hermetic workspace registration for S5-A (no env-only authority)."""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

HERMETIC_BOUNDARY_VIOLATION = "HERMETIC_BOUNDARY_VIOLATION"
REPO_ROOT_FIXTURE_REFUSED = "REPO_ROOT_FIXTURE_REFUSED"
SANDBOX_NOT_AUTHORIZED = "SANDBOX_NOT_AUTHORIZED"
PATH_COMPONENT_INVALID = "PATH_COMPONENT_INVALID"

_lock = threading.Lock()
_registered_workspaces: set[str] = set()
_authorized_sandbox: str | None = None


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


def _resolve_workspace_path(root: Path) -> Path:
    """Resolve without following final symlink (blocks symlink escape to repo)."""
    try:
        if not root.exists() and not root.is_symlink():
            resolved = root.resolve(strict=False)
        else:
            resolved = Path(os.path.realpath(root))
    except OSError as exc:
        raise HermeticBoundaryError(
            HERMETIC_BOUNDARY_VIOLATION,
            f"workspace path not resolvable: {root}",
        ) from exc
    if _is_repo_descendant(resolved):
        raise HermeticBoundaryError(
            REPO_ROOT_FIXTURE_REFUSED,
            "production repo path cannot be an S5 fixture workspace",
        )
    return resolved


def _assert_within_authorized_sandbox(resolved: Path) -> None:
    if _authorized_sandbox is None:
        raise HermeticBoundaryError(
            SANDBOX_NOT_AUTHORIZED,
            "S5 hermetic sandbox not authorized by test harness",
        )
    sandbox = Path(_authorized_sandbox)
    if resolved != sandbox:
        try:
            resolved.relative_to(sandbox)
        except ValueError:
            raise HermeticBoundaryError(
                HERMETIC_BOUNDARY_VIOLATION,
                "workspace outside authorized S5 hermetic sandbox",
            )


def authorize_s5_hermetic_sandbox(parent: Path) -> Path:
    """Test harness: declare parent directory that may contain registered workspaces."""
    resolved = _resolve_workspace_path(parent)
    with _lock:
        global _authorized_sandbox
        _authorized_sandbox = str(resolved)
    return resolved


def clear_s5_hermetic_sandbox_authority() -> None:
    with _lock:
        global _authorized_sandbox
        _authorized_sandbox = None


def _normalize_workspace(root: Path) -> Path:
    resolved = _resolve_workspace_path(root)
    _assert_within_authorized_sandbox(resolved)
    return resolved


def register_hermetic_s5_workspace(root: Path) -> Path:
    """Register an isolated workspace for S5-A adapters and fixture writers."""
    resolved = _normalize_workspace(root)
    with _lock:
        _registered_workspaces.add(str(resolved))
    return resolved


def unregister_hermetic_s5_workspace(root: Path) -> None:
    resolved = _resolve_workspace_path(root)
    with _lock:
        _registered_workspaces.discard(str(resolved))


def clear_hermetic_s5_workspaces() -> None:
    with _lock:
        global _authorized_sandbox
        _registered_workspaces.clear()
        _authorized_sandbox = None


def assert_hermetic_s5_workspace(root: Path) -> Path:
    """Raise when root is not an explicitly registered hermetic workspace."""
    resolved = _normalize_workspace(root)
    with _lock:
        if str(resolved) not in _registered_workspaces:
            raise HermeticBoundaryError(
                HERMETIC_BOUNDARY_VIOLATION,
                "workspace not registered for S5-A hermetic execution",
            )
    return resolved


def s5_hermetic_adapters_enabled(*, root: Path) -> bool:
    """True only for explicitly registered non-REPO workspaces."""
    try:
        resolved = _normalize_workspace(root)
    except HermeticBoundaryError:
        return False
    with _lock:
        return str(resolved) in _registered_workspaces


def validate_s5_path_component(value: str, field: str) -> str:
    """Reject traversal or separator injection in receipt path components."""
    name = value.strip()
    if not name or name in {".", ".."}:
        raise HermeticBoundaryError(
            PATH_COMPONENT_INVALID,
            f"invalid {field} path component",
        )
    for ch in ("/", "\\", "\0"):
        if ch in name:
            raise HermeticBoundaryError(
                PATH_COMPONENT_INVALID,
                f"invalid {field} path component",
            )
    return name


def assert_receipt_path_contained(
    receipt_path: Path,
    *,
    workspace_root: Path,
) -> Path:
    """Receipt carrier must stay under the registered hermetic workspace."""
    ws = _resolve_workspace_path(workspace_root)
    try:
        resolved = Path(os.path.realpath(receipt_path))
    except OSError as exc:
        raise HermeticBoundaryError(
            HERMETIC_BOUNDARY_VIOLATION,
            "receipt path not resolvable",
        ) from exc
    if resolved != ws:
        try:
            resolved.relative_to(ws)
        except ValueError:
            raise HermeticBoundaryError(
                HERMETIC_BOUNDARY_VIOLATION,
                "receipt path escapes hermetic workspace",
            )
    return resolved


@contextlib.contextmanager
def hermetic_s5_workspace(root: Path):
    """Test dependency injection: authorize sandbox and register workspace."""
    authorize_s5_hermetic_sandbox(root)
    register_hermetic_s5_workspace(root)
    try:
        yield root
    finally:
        unregister_hermetic_s5_workspace(root)
        clear_s5_hermetic_sandbox_authority()
