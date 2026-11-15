"""Пути OPSX DI: корень репозитория и каталоги runtime/configs.

Не зависит от ``opsx_runtime`` на уровне импорта модуля.
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

from pathlib import Path

_DI_PACKAGE_ROOT = Path(__file__).resolve().parent
_OPSX_TREE_ROOT = _DI_PACKAGE_ROOT.parent
_DEFAULT_REPO_ROOT = project_root()
def default_repo_root() -> Path:
    """Корень репозитория по расположению дерева ``opsx/`` (родитель ``opsx``)."""
    return _DEFAULT_REPO_ROOT.resolve()


def opsx_runtime_root(*, repo_root: Path | None = None) -> Path:
    """Каталог ``opsx/opsx_runtime`` для ``sys.path``."""
    root = (repo_root or default_repo_root()).resolve()
    return resource_path(root, "opsx", "opsx_runtime")


def opsx_configs_root(*, repo_root: Path | None = None) -> Path:
    """Каталог ``opsx/configs`` для ``sys.path``."""
    root = (repo_root or default_repo_root()).resolve()
    return resource_path(root, "opsx", "configs")
