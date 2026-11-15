"""Подключение ``opsx_runtime`` и configs к ``sys.path`` для фабрик DI.

Вызывается лениво из контейнеров перед импортом flat-модулей runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path

from opsx.di._paths import opsx_configs_root, opsx_runtime_root


def ensure_opsx_runtime_importable(*, repo_root: Path | None = None) -> Path:
    """Добавить ``opsx/opsx_runtime`` и ``opsx/configs`` в ``sys.path`` при необходимости.

    Returns:
        Канонический путь к ``opsx/opsx_runtime``.
    """
    runtime = opsx_runtime_root(repo_root=repo_root)
    runtime_s = str(runtime)
    if runtime_s not in sys.path:
        sys.path.insert(0, runtime_s)
    configs = opsx_configs_root(repo_root=repo_root)
    configs_s = str(configs)
    if configs_s not in sys.path:
        sys.path.insert(0, configs_s)
    return runtime


def ensure_cursor_sdk_importable(*, repo_root: Path | None = None) -> None:
    """Проброс к runtime bootstrap (venv ``cursor_sdk``)."""
    ensure_opsx_runtime_importable(repo_root=repo_root)
    from runtime_bootstrap import ensure_cursor_sdk_importable as _ensure

    _ensure()
