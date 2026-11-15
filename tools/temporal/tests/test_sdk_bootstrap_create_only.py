"""Temporal adapter T9–T12: create-only bootstrap via bootstrap_local_agent."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = ROOT.parents[1]
CURSOR_EXECUTOR_ROOT = GAME_ROOT / "tools" / "cursor_executor"
for path in (str(ROOT), str(CURSOR_EXECUTOR_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from cursor_executor.schemas import BootstrapLocalAgentResult
from sdk_cursor_adapter import (
    SdkCursorAdapter,
    configure_sdk_cursor_adapter,
    reset_sdk_cursor_adapter_config,
)
from session_lifecycle_contract import SESSION_BOOTSTRAP_PROCESS_TIMEOUT_SECONDS


def test_t9_adapter_calls_bootstrap_local_agent_not_execute() -> None:
    calls: list[str] = []

    class _Executor:
        async def bootstrap_local_agent(self, request, *, execution_id):
            calls.append("bootstrap_local_agent")
            assert execution_id.endswith("agent-bootstrap")
            return BootstrapLocalAgentResult(
                agent_id="agent-9",
                timings_ms={"total_bootstrap_ms": 5},
            )

        async def execute(self, request):
            calls.append("execute")
            raise AssertionError("execute must not run for session bootstrap")

    configure_sdk_cursor_adapter(executor_factory=lambda: _Executor())
    adapter = SdkCursorAdapter(".", workflow_key="wf-t9", model_config=None)
    with patch.object(adapter, "_route", return_value=MagicMock()):
        with patch("sdk_cursor_adapter._run_async", side_effect=lambda c: asyncio.run(c)):
            agent_id = adapter.bootstrap_agent()
    assert agent_id == "agent-9"
    assert calls == ["bootstrap_local_agent"]
    reset_sdk_cursor_adapter_config()


def test_t10_bootstrap_timeout_from_contract_a5() -> None:
    captured: dict = {}

    class _Executor:
        async def bootstrap_local_agent(self, request, *, execution_id):
            captured["timeout_seconds"] = request.timeout_seconds
            return BootstrapLocalAgentResult(agent_id="agent-10", timings_ms={})

    configure_sdk_cursor_adapter(executor_factory=lambda: _Executor())
    adapter = SdkCursorAdapter(
        ".",
        workflow_key="wf-timeout",
        model_config=None,
        timeout_seconds=None,
    )
    with patch.object(adapter, "_route", return_value=MagicMock()):
        with patch("sdk_cursor_adapter._run_async", side_effect=lambda c: asyncio.run(c)):
            adapter.bootstrap_agent()
    assert captured.get("timeout_seconds") == SESSION_BOOTSTRAP_PROCESS_TIMEOUT_SECONDS
    reset_sdk_cursor_adapter_config()


def test_t11_timings_exposed_on_adapter() -> None:
    class _Executor:
        async def bootstrap_local_agent(self, request, *, execution_id):
            return BootstrapLocalAgentResult(
                agent_id="agent-11",
                timings_ms={"catalog_ms": 1, "total_bootstrap_ms": 2},
            )

    configure_sdk_cursor_adapter(executor_factory=lambda: _Executor())
    adapter = SdkCursorAdapter(".", workflow_key="wf-t11")
    with patch.object(adapter, "_route", return_value=MagicMock()):
        with patch("sdk_cursor_adapter._run_async", side_effect=lambda c: asyncio.run(c)):
            adapter.bootstrap_agent()
    assert adapter.last_bootstrap_timings_ms()["catalog_ms"] == 1
    reset_sdk_cursor_adapter_config()


def test_t12_bootstrap_idempotent_in_memory_agent() -> None:
    class _Executor:
        async def bootstrap_local_agent(self, request, *, execution_id):
            return BootstrapLocalAgentResult(agent_id="agent-12", timings_ms={})

    configure_sdk_cursor_adapter(executor_factory=lambda: _Executor())
    adapter = SdkCursorAdapter(".", workflow_key="wf-t12")
    with patch.object(adapter, "_route", return_value=MagicMock()):
        with patch("sdk_cursor_adapter._run_async", side_effect=lambda c: asyncio.run(c)):
            first = adapter.bootstrap_agent()
            second = adapter.bootstrap_agent()
    assert first == second == "agent-12"
    reset_sdk_cursor_adapter_config()
