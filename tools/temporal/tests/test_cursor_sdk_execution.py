"""Cursor SDK execution activity + workflow contract (deterministic / fake SDK)."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = ROOT.parents[1]
CURSOR_EXECUTOR_ROOT = GAME_ROOT / "tools" / "cursor_executor"
CURSOR_EXECUTOR_TESTS = CURSOR_EXECUTOR_ROOT / "tests"
for path in (str(ROOT), str(CURSOR_EXECUTOR_ROOT), str(CURSOR_EXECUTOR_TESTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

from config import TEMPORAL_TASK_QUEUE
from cursor_execution_activity import (
    configure_cursor_execution,
    execute_cursor,
    reconcile_cursor,
    reset_cursor_execution_config,
)
from cursor_executor.executor import CursorExecutor
from cursor_executor.ledger import LedgerStore
from cursor_executor.schemas import ModelRoute, RuntimeKind, WritePolicy
from cursor_executor.sdk_client import SdkRunOutcome
from fake_sdk import FakeSdkClient
from workflow import CursorSdkExecutionWorkflow


def _catalog_model() -> dict:
    return {
        "id": "gpt-5.6-sol",
        "displayName": "GPT",
        "parameters": [
            {"id": "context", "values": [{"value": "272k"}, {"value": "128k"}]},
            {"id": "reasoning", "values": [{"value": "high"}, {"value": "low"}]},
            {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
        ],
    }


def _route_payload() -> dict:
    return {
        "model_id": "gpt-5.6-sol",
        "params": {"context": "272k", "reasoning": "high", "fast": "false"},
    }


def _payload(**overrides) -> dict:
    base = {
        "execution_id": "orch-exec-test-1",
        "orchestration_execution_id": "orch-correlation-1",
        "correlation_id": "corr-1",
        "workspace": str(GAME_ROOT),
        "runtime": "LOCAL",
        "model_route": _route_payload(),
        "prompt": "OPSX_SDK_PROCESS_READONLY_OK",
        "write_policy": "READ_ONLY",
        "timeout_seconds": 120,
    }
    base.update(overrides)
    return base


@pytest.fixture
def executor_harness(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    ledger = LedgerStore(root=Path(tmp.name) / "ledger")
    fake = FakeSdkClient(catalog=[_catalog_model()])
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test_key_for_fake_runs")
    configure_cursor_execution(
        executor_factory=lambda: CursorExecutor(sdk=fake, ledger=ledger),
        ledger_root=Path(tmp.name) / "ledger",
    )
    yield fake, ledger
    reset_cursor_execution_config()
    tmp.cleanup()


@pytest.mark.asyncio
async def test_execute_cursor_success_is_execution_only(executor_harness):
    fake, _ledger = executor_harness
    fake.next_run = SdkRunOutcome(
        run_id="run-ok",
        agent_id="agent-ok",
        sdk_status="finished",
        result_text="OPSX_SDK_PROCESS_READONLY_OK",
        observed_route=ModelRoute(
            "gpt-5.6-sol",
            {"context": "272k", "reasoning": "high", "fast": "false"},
        ),
        duration_ms=5,
    )
    env = ActivityEnvironment()
    result = await env.run(execute_cursor, _payload())
    assert result["execution_status"] == "EXECUTION_SUCCEEDED"
    assert result["engineering_pass_inferred"] is False
    assert result["legacy_transport_evidence"]["agent_cli_invocation_count"] == 0
    assert result["agent_id"] == "agent-fake-1"
    assert result["run_id"] == "run-ok"
    assert result["model_match"] is True


@pytest.mark.asyncio
async def test_activity_retry_same_execution_id_no_duplicate_agent(executor_harness):
    fake, _ledger = executor_harness
    fake.next_run = SdkRunOutcome(
        run_id="run-idem",
        agent_id="agent-idem",
        sdk_status="finished",
        result_text="OPSX_SDK_PROCESS_READONLY_OK",
        observed_route=ModelRoute(
            "gpt-5.6-sol",
            {"context": "272k", "reasoning": "high", "fast": "false"},
        ),
        duration_ms=4,
    )
    env = ActivityEnvironment()
    first = await env.run(execute_cursor, _payload(execution_id="idem-1"))
    second = await env.run(execute_cursor, _payload(execution_id="idem-1"))
    assert fake.create_calls == 1
    assert fake.send_calls == 1
    assert first["agent_id"] == second["agent_id"]
    assert first["run_id"] == second["run_id"]
    assert second["reconciliation_state"] == "TERMINAL_FROM_LEDGER"


@pytest.mark.asyncio
async def test_reconcile_cursor_recovers_without_create(executor_harness):
    fake, ledger = executor_harness
    fake.next_run = SdkRunOutcome(
        run_id="run-rec",
        agent_id="agent-rec",
        sdk_status="finished",
        result_text="OPSX_SDK_PROCESS_READONLY_OK",
        observed_route=ModelRoute(
            "gpt-5.6-sol",
            {"context": "272k", "reasoning": "high", "fast": "false"},
        ),
        duration_ms=2,
    )
    env = ActivityEnvironment()
    executed = await env.run(execute_cursor, _payload(execution_id="rec-1"))
    fake.create_calls = 0
    fake.send_calls = 0
    configure_cursor_execution(
        executor_factory=lambda: CursorExecutor(sdk=fake, ledger=ledger),
        ledger_root=ledger.root,
    )
    recovered = await env.run(
        reconcile_cursor,
        {"execution_id": "rec-1", "orchestration_execution_id": "orch-rec"},
    )
    assert fake.create_calls == 0
    assert fake.send_calls == 0
    assert recovered["run_id"] == executed["run_id"]


@pytest.mark.asyncio
async def test_execute_cursor_mutating_write_policy(executor_harness):
    fake, _ledger = executor_harness
    fake.next_run = SdkRunOutcome(
        run_id="run-mut",
        agent_id="agent-mut",
        sdk_status="finished",
        result_text="mutation ok",
        observed_route=ModelRoute(
            "gpt-5.6-sol",
            {"context": "272k", "reasoning": "high", "fast": "false"},
        ),
        duration_ms=8,
    )
    env = ActivityEnvironment()
    result = await env.run(
        execute_cursor,
        _payload(
            execution_id="mutating-orch-1",
            write_policy="MUTATING",
            prompt="create proof file",
        ),
    )
    assert result["execution_status"] == "EXECUTION_SUCCEEDED"
    assert result["write_policy"] == "MUTATING"
    assert result["write_policy_enforcement"] == "SDK_DEFAULT_TOOLSET"


@pytest.mark.asyncio
async def test_workflow_invokes_execute_cursor_activity(executor_harness):
    fake, _ledger = executor_harness
    fake.next_run = SdkRunOutcome(
        run_id="run-wf",
        agent_id="agent-wf",
        sdk_status="finished",
        result_text="OPSX_SDK_PROCESS_READONLY_OK",
        observed_route=ModelRoute(
            "gpt-5.6-sol",
            {"context": "272k", "reasoning": "high", "fast": "false"},
        ),
        duration_ms=3,
    )
    async with await WorkflowEnvironment.start_time_skipping() as wf_env:
        async with Worker(
            wf_env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[CursorSdkExecutionWorkflow],
            activities=[execute_cursor, reconcile_cursor],
        ):
            result = await wf_env.client.execute_workflow(
                CursorSdkExecutionWorkflow.run,
                _payload(execution_id="wf-exec-1"),
                id="cursor-sdk-wf-test",
                task_queue=TEMPORAL_TASK_QUEUE,
            )
    assert result["execution_status"] == "EXECUTION_SUCCEEDED"
    assert result["workflow_semantics"] == "EXECUTION_TRANSPORT_ONLY"
    assert result["engineering_pass_inferred"] is False
    assert fake.create_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sdk_status,terminal_class,execution_status",
    [
        ("error", "FAILED", "EXECUTION_FAILED"),
        ("cancelled", "CANCELLED", "EXECUTION_CANCELLED"),
    ],
)
async def test_failure_classes_propagate(
    executor_harness, sdk_status, terminal_class, execution_status
):
    fake, _ledger = executor_harness
    fake.next_run = SdkRunOutcome(
        run_id="run-fail",
        agent_id="agent-fail",
        sdk_status=sdk_status,
        result_text="",
        observed_route=ModelRoute(
            "gpt-5.6-sol",
            {"context": "272k", "reasoning": "high", "fast": "false"},
        ),
        duration_ms=1,
    )
    env = ActivityEnvironment()
    result = await env.run(execute_cursor, _payload(execution_id=f"fail-{sdk_status}"))
    assert result["terminal_class"] == terminal_class
    assert result["execution_status"] == execution_status
    assert result["execution_status"] != "EXECUTION_SUCCEEDED"


@pytest.mark.asyncio
async def test_unknown_does_not_become_success(executor_harness):
    fake, _ledger = executor_harness
    fake.timeout_on_send = True
    env = ActivityEnvironment()
    result = await env.run(execute_cursor, _payload(execution_id="unknown-1"))
    assert result["execution_status"] == "EXECUTION_UNKNOWN"
    assert result["terminal_class"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_blocked_auth_missing(executor_harness, monkeypatch):
    from cursor_executor.auth import AuthResolution, AuthSource
    import cursor_executor.executor as executor_module

    monkeypatch.setattr(
        executor_module,
        "resolve_auth",
        lambda: AuthResolution(
            api_key=None,
            source=AuthSource.NONE,
            process_env_key_present=False,
            user_env_key_present=False,
        ),
    )
    reset_cursor_execution_config()
    tmp = tempfile.TemporaryDirectory()
    ledger = LedgerStore(root=Path(tmp.name) / "ledger")
    fake = FakeSdkClient(catalog=[_catalog_model()])
    configure_cursor_execution(
        executor_factory=lambda: CursorExecutor(sdk=fake, ledger=ledger),
        ledger_root=ledger.root,
    )
    env = ActivityEnvironment()
    result = await env.run(execute_cursor, _payload(execution_id="blocked-auth"))
    assert result["execution_status"] == "EXECUTION_BLOCKED"
    assert result["terminal_class"] == "BLOCKED"
    tmp.cleanup()
    reset_cursor_execution_config()


@pytest.mark.asyncio
async def test_model_route_mismatch_non_success(executor_harness):
    fake, _ledger = executor_harness
    fake.next_run = SdkRunOutcome(
        run_id="run-mismatch",
        agent_id="agent-mismatch",
        sdk_status="finished",
        result_text="OPSX_SDK_PROCESS_READONLY_OK",
        observed_route=ModelRoute("gpt-5.6-sol", {"context": "128k", "reasoning": "high", "fast": "false"}),
        duration_ms=1,
    )
    env = ActivityEnvironment()
    result = await env.run(execute_cursor, _payload(execution_id="mismatch-1"))
    assert result["execution_status"] == "EXECUTION_FAILED"
    assert result.get("error", {}).get("class") == "MODEL_ROUTE_MISMATCH"


@pytest.mark.asyncio
async def test_execution_id_conflict_non_success(executor_harness):
    fake, _ledger = executor_harness
    fake.next_run = SdkRunOutcome(
        run_id="run-conflict",
        agent_id="agent-conflict",
        sdk_status="finished",
        result_text="OPSX_SDK_PROCESS_READONLY_OK",
        observed_route=ModelRoute(
            "gpt-5.6-sol",
            {"context": "272k", "reasoning": "high", "fast": "false"},
        ),
        duration_ms=1,
    )
    env = ActivityEnvironment()
    await env.run(execute_cursor, _payload(execution_id="conflict-1"))
    conflict = await env.run(
        execute_cursor,
        _payload(execution_id="conflict-1", prompt="different prompt body"),
    )
    assert conflict["execution_status"] == "EXECUTION_BLOCKED"
    assert conflict.get("error", {}).get("class") == "EXECUTION_ID_CONFLICT"
