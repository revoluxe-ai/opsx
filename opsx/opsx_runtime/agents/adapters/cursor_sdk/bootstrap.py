"""Bootstrap ``cursor_sdk`` для фабрик пакета ``agents.adapters.cursor_sdk``.

Обёртка над ``runtime_bootstrap``: composition root вызывает до bridge Resource.
"""

from __future__ import annotations

from pathlib import Path

from .wheel_bootstrap import ensure_cursor_sdk_importable as _ensure_wheel


def ensure_cursor_sdk_importable(*, repo_root: Path | None = None) -> None:
    """Подключить wheel ``cursor_sdk`` из LangGraph venv, не ломая Poetry venv gates."""
    _ensure_wheel(repo_root=repo_root)
