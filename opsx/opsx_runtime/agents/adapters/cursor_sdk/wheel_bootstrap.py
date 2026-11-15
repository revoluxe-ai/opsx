"""Подключение wheel ``cursor_sdk`` из LangGraph venv без затенения Poetry gates."""

from __future__ import annotations

from opsx.project_context import resource_path

import os
import sys
from pathlib import Path

from runtime_bootstrap import (
    REPO_ROOT,
    _iter_langgraph_site_candidates,
    _langgraph_runtime_root,
    _langgraph_venv_dir,
    _native_wheels_usable,
    langgraph_runtime_python,
)


def langgraph_runtime_site_packages(*, repo_root: Path | None = None) -> Path:
    """Первый пригодный site-packages каталог langgraph runtime с wheel провайдера."""
    for site in _iter_langgraph_site_candidates(repo_root=repo_root):
        if site.is_dir() and (site / "cursor_sdk").is_dir() and _native_wheels_usable(site):
            return site
    candidates = _iter_langgraph_site_candidates(repo_root=repo_root)
    return candidates[0] if candidates else _langgraph_runtime_root(repo_root=repo_root) / ".venv"


def apply_cursor_sdk_bridge_temp_dir(*, repo_root: Path | None = None) -> Path:
    """Перенаправить TEMP/TMP/TMPDIR для Node ``cursor-sdk-bridge``.

    На Windows Cursor/CI часто наследуют системный ``C:\\Temp`` с EPERM на
    ``mkdtemp``; тот же каталог, что ``tools.temporal.config.apply_cursor_sdk_bridge_temp_dir``.
    """

    repo = repo_root or REPO_ROOT
    bridge_tmp = resource_path(repo, "opsx", "runs", "adapters", "temporary")
    bridge_tmp.mkdir(parents=True, exist_ok=True)
    resolved = str(bridge_tmp.resolve())
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = resolved
    return bridge_tmp


def _isolated_cursor_sdk_path(site: Path) -> Path | None:
    """Каталог, в котором есть только ``cursor_sdk``, без чужих wheel.

    Windows ``Lib/site-packages`` содержит ``pydantic`` без Linux-расширения
    ``pydantic_core._pydantic_core``. Добавлять весь каталог в ``sys.path`` нельзя.
    """
    package = site / "cursor_sdk"
    if not package.is_dir():
        return None
    isolated = Path(os.environ.get("TMPDIR", "/tmp")) / "stroy-cursor-sdk-path"
    isolated.mkdir(parents=True, exist_ok=True)
    link = isolated / "cursor_sdk"
    if link.is_symlink() or link.exists():
        if not link.is_symlink() or link.resolve() != package.resolve():
            if link.is_symlink() or link.is_file():
                link.unlink()
            elif link.is_dir() and not link.is_symlink():
                return None
    if not link.exists():
        link.symlink_to(package, target_is_directory=True)
    return isolated


def ensure_cursor_sdk_importable(*, repo_root: Path | None = None) -> None:
    """Подключить ``cursor_sdk``, не затеняя пакеты текущего интерпретатора."""
    apply_cursor_sdk_bridge_temp_dir(repo_root=repo_root)
    try:
        import cursor_sdk  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    repo = repo_root or REPO_ROOT
    for site in _iter_langgraph_site_candidates(repo_root=repo):
        if not site.is_dir() or not (site / "cursor_sdk").is_dir():
            continue

        added_paths: list[str] = []
        if sys.platform == "win32":
            if not _native_wheels_usable(site):
                continue
            site_str = str(site)
            if site_str not in sys.path:
                sys.path.insert(0, site_str)
                added_paths.append(site_str)
        elif _native_wheels_usable(site):
            site_str = str(site)
            if site_str not in sys.path:
                sys.path.insert(0, site_str)
                added_paths.append(site_str)
        else:
            isolated = _isolated_cursor_sdk_path(site)
            if isolated is None:
                continue
            isolated_str = str(isolated)
            if isolated_str not in sys.path:
                sys.path.append(isolated_str)
                added_paths.append(isolated_str)

        try:
            import cursor_sdk  # noqa: F401
            return
        except ModuleNotFoundError:
            for path_str in added_paths:
                if path_str in sys.path:
                    sys.path.remove(path_str)

    lg = _langgraph_runtime_root(repo_root=repo)
    venv_dir = _langgraph_venv_dir(repo_root=repo)
    py = langgraph_runtime_python(repo_root=repo)
    raise ModuleNotFoundError(
        "cursor_sdk not found. Bootstrap once:\n"
        f"  python3.12 -m venv {venv_dir}\n"
        f"  {py} -m pip install -r {lg / 'requirements.txt'}\n"
        "See tools/langgraph_runtime/README.md",
    )
