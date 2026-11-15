"""Корень репозитория и нормализация ``workspace`` / ``repository_path`` для LangGraph PP.

Старые checkpoint'и с Windows worktree (``C:\\Users\\...``) на Linux давали склеенный
путь ``/wsl/root/C:\\Users\\...`` при ``Path(linux_root) / windows_path``.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import re
from pathlib import Path
from typing import Any, Mapping

_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")


def resolve_opsx_repository_root(candidate: Path) -> Path:
    """Проверить и вернуть канонический корень репозитория (``opsx`` + checkout).

    Args:
        candidate: Путь, обычно каталог с ``run_opsx_main.py``.

    Returns:
        ``candidate.resolve()``.

    Raises:
        ValueError: если это не корень OPSX-репозитория.
    """
    root = candidate.resolve()
    if not (resource_path(root, "opsx")).is_dir():
        raise ValueError(f"OPSX repository root not found (missing opsx): {root}")
    return root


def _looks_like_windows_abs(path: str) -> bool:
    return bool(_WIN_ABS.match(path.strip()))


def _looks_like_glued_linux_windows(path: str, repo: Path) -> bool:
    """``/home/.../repo/C:\\Users\\...`` после ошибочного join."""
    normalized = path.replace("\\", "/")
    repo_s = str(repo.resolve()).replace("\\", "/")
    if not normalized.startswith(repo_s + "/"):
        return False
    tail = normalized[len(repo_s) + 1 :]
    return _looks_like_windows_abs(tail) or tail.startswith("C:/") or "/C:" in tail


def normalize_workspace_path(
    workspace: object,
    repository_path: object,
) -> Path:
    """Привести ``workspace`` к существующему каталогу; Windows/glued → ``repository_path``.

    Args:
        workspace: Поле GraphState (может быть Windows worktree из старого checkpoint).
        repository_path: Корень репозитория для текущего прогона.

    Returns:
        Каталог, существующий на диске (обычно корень репозитория).
    """
    repo = resolve_opsx_repository_root(Path(str(repository_path)))
    ws_raw = str(workspace or "").strip()
    if not ws_raw:
        return repo
    if _looks_like_windows_abs(ws_raw) or _looks_like_glued_linux_windows(ws_raw, repo):
        return repo
    ws = Path(ws_raw)
    if ws.is_dir():
        return ws.resolve()
    if not ws.is_absolute():
        candidate = (resource_path(repo, ws)).resolve()
        if candidate.is_dir():
            return candidate
    return repo


def graph_repo_path_patch(state: Mapping[str, Any], repo_root: Path) -> dict[str, str]:
    """Патч ``repository_path`` и ``workspace`` для GraphState / checkpoint update."""
    repo = resolve_opsx_repository_root(repo_root)
    repo_s = str(repo)
    workspace = normalize_workspace_path(state.get("workspace"), repo)
    return {
        "repository_path": repo_s,
        "workspace": str(workspace),
    }
