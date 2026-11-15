"""Единый runtime для OPSX CLIs: LangGraph venv paths и регистрация фабрик адаптера.

Poetry project venv (gates: pytest/pyright/mypy) и LangGraph runtime venv — разные
интерпретаторы по задумке repair README; отдельное окружение на каждый запуск не
создаётся.

Единственный локальный venv LangGraph: ``tools/langgraph_runtime/.venv`` (не в git).
Linux — ``lib/pythonX.Y/site-packages``; Windows — ``Lib/site-packages`` + ``Scripts``.
На Linux чужой Windows wheel не подмешивается целиком в ``sys.path``.
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import os
import sys
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parent
REPO_ROOT = project_root()
def _python_tag() -> str:
    return f"python{sys.version_info.major}.{sys.version_info.minor}"


def _langgraph_runtime_root(*, repo_root: Path | None = None) -> Path:
    repo = repo_root or REPO_ROOT
    return resource_path(repo, "tools", "langgraph_runtime")


def _langgraph_venv_dir(*, repo_root: Path | None = None) -> Path:
    return _langgraph_runtime_root(repo_root=repo_root) / ".venv"


def _langgraph_site_packages(venv: Path, py_tag: str) -> Path:
    if os.name == "nt":
        return venv / "Lib" / "site-packages"
    return venv / "lib" / py_tag / "site-packages"


def _iter_langgraph_site_candidates(*, repo_root: Path | None = None) -> list[Path]:
    """Кандидаты site-packages в порядке предпочтения для текущей ОС."""
    repo = repo_root or REPO_ROOT
    py_tag = _python_tag()
    venv = _langgraph_venv_dir(repo_root=repo)
    candidates: list[Path] = [_langgraph_site_packages(venv, py_tag)]
    current_venv = Path(os.environ.get("VIRTUAL_ENV", sys.prefix))
    candidates.append(_langgraph_site_packages(current_venv, py_tag))
    return candidates


def _native_wheels_usable(site: Path) -> bool:
    """Отсечь Windows-only wheels (pydantic без .so на Linux)."""
    pc = site / "pydantic_core"
    if not pc.is_dir():
        return True
    if os.name == "nt":
        return any(pc.glob("_pydantic_core*.pyd"))
    return any(pc.glob("_pydantic_core*.so"))


def langgraph_runtime_python(*, repo_root: Path | None = None) -> Path:
    """Рекомендуемый интерпретатор для verify/repair LangGraph."""
    venv = _langgraph_venv_dir(repo_root=repo_root)
    if os.name == "nt":
        win_py = venv / "Scripts" / "python.exe"
        if win_py.is_file():
            return win_py
    else:
        linux_py = venv / "bin" / "python"
        if linux_py.is_file():
            return linux_py
    return Path(sys.executable)


def langgraph_runtime_site_packages(*, repo_root: Path | None = None) -> Path:
    """Первый пригодный site-packages каталог langgraph runtime (langgraph wheel)."""
    for site in _iter_langgraph_site_candidates(repo_root=repo_root):
        if site.is_dir() and (site / "langgraph").is_dir() and _native_wheels_usable(site):
            return site
    candidates = _iter_langgraph_site_candidates(repo_root=repo_root)
    return candidates[0] if candidates else _langgraph_runtime_root(repo_root=repo_root) / ".venv"


def ensure_adapter_factories_registered() -> None:
    """Register provider adapter factories (composition root outside workflows scan)."""
    from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered as _ensure

    _ensure()


def ensure_provider_wheel_importable(*, repo_root: Path | None = None) -> None:
    """Подключить wheel провайдера через зарегистрированный bootstrap hook."""
    from agents.adapters.provider_bootstrap import ensure_provider_wheel_importable as _ensure

    _ensure(repo_root=repo_root)
