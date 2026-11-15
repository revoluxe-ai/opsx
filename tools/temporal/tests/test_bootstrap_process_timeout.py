"""A5: bootstrap CursorExecutionRequest uses contract process timeout."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = ROOT.parents[1]
CURSOR_EXECUTOR_ROOT = GAME_ROOT / "tools" / "cursor_executor"
for path in (str(ROOT), str(CURSOR_EXECUTOR_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from session_lifecycle_contract import SESSION_BOOTSTRAP_PROCESS_TIMEOUT_SECONDS
from sdk_cursor_adapter import SdkCursorAdapter, configure_sdk_cursor_adapter, reset_sdk_cursor_adapter_config


def test_bootstrap_request_timeout_from_contract_when_unset_a5() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "test_sdk_bootstrap_create_only",
        ROOT / "tests" / "test_sdk_bootstrap_create_only.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.test_t10_bootstrap_timeout_from_contract_a5()
