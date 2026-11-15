"""Composition root для локального Owner-named bug repair UI."""

from __future__ import annotations

import os
from pathlib import Path

from agents.adapters.provider_bootstrap import ensure_provider_wheel_importable
from runtime_bootstrap import ensure_adapter_factories_registered


def apply_named_bug_repair_process_env() -> None:
    """Перед стартом UI/repair: HTTP/1.1 bridge и небуферизованный вывод (bash/PowerShell).

    Owner не обязан вручную ``export CURSOR_USE_HTTP1=1`` — без этого на Windows
  часты ``httpx.ReadError`` / ``Bridge request failed`` в model adapter.
    """
    _ = os.environ.setdefault("CURSOR_USE_HTTP1", "1")
    _ = os.environ.setdefault("PYTHONUNBUFFERED", "1")
    # Как verify-impl: sqlite scoped store, не глобальный jsonl-store моста.
    _ = os.environ.setdefault("OPSX_CURSOR_SDK_STORE", "sqlite")


def prepare_named_bug_repair_runtime(*, repo_root: Path) -> None:
    """Зарегистрировать adapter factories и подключить provider wheel (как verify/repair CLI).

    Args:
        repo_root: Корень репозитория с ``opsx/`` и ``tools/cursor_executor/``.
    """
    apply_named_bug_repair_process_env()
    ensure_provider_wheel_importable(repo_root=repo_root.resolve())
    ensure_adapter_factories_registered()