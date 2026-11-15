"""Temporal activity boundary for Cursor Python SDK execution (CursorExecutor V0.1).

Transport-only: maps orchestration payloads to CursorExecutionRequest, invokes
CursorExecutor, returns machine-readable execution evidence. Does not infer
engineering PASS or OPSX lifecycle transitions.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from temporalio import activity

_GAME_ROOT = Path(__file__).resolve().parents[2]
_CURSOR_EXECUTOR_ROOT = _GAME_ROOT / "tools" / "cursor_executor"
if str(_CURSOR_EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_CURSOR_EXECUTOR_ROOT))

from cursor_executor.executor import CursorExecutor  # noqa: E402
from cursor_executor.ledger import LedgerStore  # noqa: E402
from cursor_executor.schemas import (  # noqa: E402
    AdapterTerminalState,
    CursorExecutionRequest,
    CursorExecutionResult,
    RuntimeKind,
    WritePolicy,
    ModelRoute,
)

_EXECUTION_STATUS_MAP = {
    AdapterTerminalState.SUCCEEDED: "EXECUTION_SUCCEEDED",
    AdapterTerminalState.FAILED: "EXECUTION_FAILED",
    AdapterTerminalState.BLOCKED: "EXECUTION_BLOCKED",
    AdapterTerminalState.CANCELLED: "EXECUTION_CANCELLED",
    AdapterTerminalState.UNKNOWN: "EXECUTION_UNKNOWN",
}


@dataclass
class LegacyTransportCounters:
    cli_invocation_count: int = 0
    task_invocation_count: int = 0


_LEGACY_COUNTERS = LegacyTransportCounters()
_EXECUTOR_FACTORY: Callable[[], CursorExecutor] | None = None
_LEDGER_ROOT: Path | None = None


def configure_cursor_execution(
    *,
    executor_factory: Callable[[], CursorExecutor] | None = None,
    ledger_root: Path | str | None = None,
) -> None:
    """Test/dogfood hook: inject CursorExecutor or ledger location."""
    global _EXECUTOR_FACTORY, _LEDGER_ROOT
    _EXECUTOR_FACTORY = executor_factory
    _LEDGER_ROOT = Path(ledger_root) if ledger_root else None


def reset_cursor_execution_config() -> None:
    configure_cursor_execution(executor_factory=None, ledger_root=None)


def _default_ledger_root() -> Path:
    if _LEDGER_ROOT is not None:
        return _LEDGER_ROOT
    return _GAME_ROOT / "opsx" / "cursor-executor-ledger"


def _get_executor() -> CursorExecutor:
    if _EXECUTOR_FACTORY is not None:
        return _EXECUTOR_FACTORY()
    return CursorExecutor(ledger=LedgerStore(root=_default_ledger_root()))


@contextmanager
def _legacy_transport_guard():
    """Count legacy CLI/Task transport use during SDK activity execution."""
    import cursor_adapter

    counters = LegacyTransportCounters()
    original_cli = cursor_adapter.run_agent_command

    def _counting_cli(*args, **kwargs):
        counters.cli_invocation_count += 1
        return original_cli(*args, **kwargs)

    cursor_adapter.run_agent_command = _counting_cli
    try:
        yield counters
    finally:
        cursor_adapter.run_agent_command = original_cli


def request_from_activity_payload(payload: dict[str, Any]) -> CursorExecutionRequest:
    """Build CursorExecutionRequest from orchestration JSON (model_id + params)."""
    route = payload.get("model_route") or {}
    if not isinstance(route, dict):
        raise ValueError("model_route must be an object")
    write_policy_raw = str(payload.get("write_policy", "READ_ONLY")).upper()
    runtime_raw = str(payload.get("runtime", "LOCAL")).upper()
    return CursorExecutionRequest(
        execution_id=str(payload["execution_id"]),
        correlation_id=payload.get("correlation_id"),
        workspace=str(payload["workspace"]),
        runtime=RuntimeKind(runtime_raw),
        model_route=ModelRoute.from_json(route),
        prompt=str(payload["prompt"]),
        write_policy=WritePolicy(write_policy_raw),
        timeout_seconds=payload.get("timeout_seconds"),
        metadata=dict(payload.get("metadata") or {}),
    )


def activity_response_from_result(
    result: CursorExecutionResult,
    *,
    orchestration_execution_id: str | None,
    legacy: LegacyTransportCounters,
) -> dict[str, Any]:
    """Machine-readable activity output; execution success != engineering PASS."""
    execution_status = _EXECUTION_STATUS_MAP.get(
        result.terminal_class, "EXECUTION_UNKNOWN"
    )
    body = result.to_json()
    body["execution_status"] = execution_status
    body["orchestration_execution_id"] = orchestration_execution_id or result.execution_id
    body["legacy_transport_evidence"] = {
        "agent_cli_invocation_count": legacy.cli_invocation_count,
        "task_subagent_invocation_count": legacy.task_invocation_count,
        "cursor_adapter_execute_prompt_invoked": False,
    }
    body["engineering_pass_inferred"] = False
    return body


@activity.defn(name="execute_cursor")
async def execute_cursor(payload: dict[str, Any]) -> dict[str, Any]:
    """First attempt or idempotent retry: CursorExecutor.execute(request)."""
    request = request_from_activity_payload(payload)
    orchestration_id = str(
        payload.get("orchestration_execution_id") or request.execution_id
    )
    activity.logger.info(
        "execute_cursor execution_id=%s correlation_id=%s workspace=%s write_policy=%s",
        request.execution_id,
        request.correlation_id,
        request.workspace,
        request.write_policy.value,
    )

    executor = _get_executor()
    with _legacy_transport_guard() as legacy:
        result = await executor.execute(request)

    response = activity_response_from_result(
        result,
        orchestration_execution_id=orchestration_id,
        legacy=legacy,
    )
    activity.logger.info(
        "execute_cursor terminal=%s execution_status=%s agent_id=%s run_id=%s cli_calls=%s",
        result.terminal_class.value,
        response["execution_status"],
        result.agent_id,
        result.run_id,
        legacy.cli_invocation_count,
    )
    return response


@activity.defn(name="reconcile_cursor")
async def reconcile_cursor(payload: dict[str, Any]) -> dict[str, Any]:
    """Recovery path: CursorExecutor.reconcile(execution_id) without new agent."""
    execution_id = str(payload["execution_id"])
    request = None
    if payload.get("request"):
        request = request_from_activity_payload(payload["request"])
    orchestration_id = str(
        payload.get("orchestration_execution_id") or execution_id
    )
    activity.logger.info("reconcile_cursor execution_id=%s", execution_id)

    executor = _get_executor()
    with _legacy_transport_guard() as legacy:
        result = await executor.reconcile(execution_id, request=request)

    return activity_response_from_result(
        result,
        orchestration_execution_id=orchestration_id,
        legacy=legacy,
    )


async def run_execute_cursor_for_tests(payload: dict[str, Any]) -> dict[str, Any]:
    """Direct await helper when ActivityEnvironment is not required."""
    return await execute_cursor(payload)
