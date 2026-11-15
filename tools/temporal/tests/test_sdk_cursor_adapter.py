"""SdkCursorAdapter lane transport migration contract tests."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = ROOT.parents[1]
CURSOR_EXECUTOR_ROOT = GAME_ROOT / "tools" / "cursor_executor"
CURSOR_EXECUTOR_TESTS = CURSOR_EXECUTOR_ROOT / "tests"
for path in (str(ROOT), str(CURSOR_EXECUTOR_ROOT), str(CURSOR_EXECUTOR_TESTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

from cursor_executor.executor import CursorExecutor
from cursor_executor.ledger import LedgerStore
from cursor_executor.schemas import ModelRoute
from cursor_executor.sdk_client import SdkRunOutcome
from fake_sdk import FakeSdkClient
from sdk_cursor_adapter import SdkCursorAdapter, configure_sdk_cursor_adapter, reset_sdk_cursor_adapter_config


def _catalog() -> list[dict]:
    return [
        {
            "id": "gpt-5.6-sol",
            "displayName": "GPT",
            "parameters": [
                {"id": "context", "values": [{"value": "272k"}]},
                {"id": "reasoning", "values": [{"value": "high"}, {"value": "low"}]},
                {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
            ],
        },
        {
            "id": "composer-2.5",
            "displayName": "Composer",
            "parameters": [
                {"id": "reasoning", "values": [{"value": "low"}, {"value": "high"}]},
                {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
            ],
        },
    ]


def _run_outcome(*, text: str = "lane-ok") -> SdkRunOutcome:
    return SdkRunOutcome(
        run_id="run-lane-1",
        agent_id="agent-fake-1",
        sdk_status="finished",
        result_text=text,
        observed_route=ModelRoute(
            "gpt-5.6-sol",
            {"context": "272k", "reasoning": "high", "fast": "false"},
        ),
        duration_ms=1,
    )


@pytest.fixture
def sdk_adapter_harness(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    ledger = LedgerStore(root=Path(tmp.name) / "ledger")
    fake = FakeSdkClient(catalog=_catalog())
    fake.next_run = _run_outcome()
    monkeypatch.setenv("CURSOR_API_KEY", "cursor_test_key_for_fake_runs")
    configure_sdk_cursor_adapter(
        executor_factory=lambda: CursorExecutor(sdk=fake, ledger=ledger),
        ledger_root=Path(tmp.name) / "ledger",
    )
    cli_calls: list[list[str]] = []

    def _blocked_cli(*args, **kwargs):
        cli_calls.append(list(args[0]) if args else [])
        raise AssertionError("legacy agent CLI must not run on canonical lane path")

    with patch("cursor_adapter.run_agent_command", side_effect=_blocked_cli):
        adapter = SdkCursorAdapter(
            str(GAME_ROOT),
            workflow_key="wf-test-1",
            model_config=None,
        )
        yield adapter, fake, ledger, cli_calls
    reset_sdk_cursor_adapter_config()
    tmp.cleanup()


def test_bootstrap_and_prompt_use_sdk_not_cli(sdk_adapter_harness):
    adapter, fake, _ledger, cli_calls = sdk_adapter_harness
    agent_id = adapter.bootstrap_agent()
    assert agent_id.startswith("agent-")
    assert fake.create_calls == 1
    bootstrap_sends = fake.send_calls
    inv = adapter.execute_prompt("review prompt text", step="review")
    assert inv.raw_output == "lane-ok"
    assert fake.send_calls == bootstrap_sends + 1
    assert cli_calls == []


def test_stable_execution_id_on_retry(sdk_adapter_harness):
    adapter, fake, ledger, _cli = sdk_adapter_harness
    adapter.bootstrap_agent()
    prompt = "same prompt for idempotency"
    adapter.execute_prompt(prompt, step="review")
    first_send = fake.send_calls
    adapter.execute_prompt(prompt, step="review")
    assert fake.send_calls == first_send


def test_executor_success_does_not_imply_lane_pass(sdk_adapter_harness):
    adapter, fake, _ledger, _cli = sdk_adapter_harness
    fake.next_run = _run_outcome(text="STATUS: BUGBOT_FINDINGS\nnot a clean pass token")
    adapter.bootstrap_agent()
    inv = adapter.execute_prompt("x", step="review")
    assert inv.exit_code == 0
    assert "BUGBOT_FINDINGS" in inv.raw_output


def test_unknown_terminal_raises_transient(sdk_adapter_harness):
    adapter, fake, _ledger, _cli = sdk_adapter_harness
    fake.next_run = _run_outcome()
    adapter.bootstrap_agent()
    fake.next_run = SdkRunOutcome(
        run_id="run-u",
        agent_id="agent-fake-1",
        sdk_status="running",
        result_text="",
        observed_route=ModelRoute(
            "gpt-5.6-sol",
            {"context": "272k", "reasoning": "high", "fast": "false"},
        ),
        duration_ms=1,
    )
    adapter.bootstrap_agent()
    from cursor_adapter import CursorTransientError

    with pytest.raises(CursorTransientError):
        adapter.execute_prompt("y", step="review")


def test_restore_session_keeps_agent_id(sdk_adapter_harness):
    adapter, fake, _ledger, _cli = sdk_adapter_harness
    agent_id = adapter.bootstrap_agent()
    restored = SdkCursorAdapter(
        str(GAME_ROOT),
        session_id=agent_id,
        workflow_key="wf-test-1",
        model_config=None,
    )
    assert restored.session_id() == agent_id
    assert restored._agent_id == agent_id
