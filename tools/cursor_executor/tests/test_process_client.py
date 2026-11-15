"""Process-boundary convergence contract for production Cursor execution."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cursor_executor.process_client import (
    CursorExecutorProcessClient,
    ProcessHardTimeout,
)
from cursor_executor.bridge_local import windows_agent_subprocess_env
from cursor_executor.schemas import (
    AdapterTerminalState,
    CursorExecutionRequest,
    ModelRoute,
    RuntimeKind,
    WritePolicy,
)


def _request() -> CursorExecutionRequest:
    return CursorExecutionRequest(
        execution_id="process-timeout-test",
        correlation_id="test",
        workspace=".",
        runtime=RuntimeKind.LOCAL,
        model_route=ModelRoute(model_id="composer-2.5", params={}),
        prompt="test",
        write_policy=WritePolicy.READ_ONLY,
        timeout_seconds=1,
    )
class ProcessClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_hard_timeout_materializes_unknown_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = CursorExecutorProcessClient(
                ledger_root=Path(tmp),
                timeout_grace_seconds=0,
            )
            request = _request()
            with patch.object(
                client,
                "_run_worker",
                side_effect=ProcessHardTimeout("forced hard deadline"),
            ):
                result = await client.execute(request)

            self.assertEqual(result.terminal_class, AdapterTerminalState.UNKNOWN)
            self.assertIsNotNone(result.error)
            self.assertEqual(result.error.class_name, "PROCESS_HARD_TIMEOUT")
            self.assertEqual(result.error.stage, "process_boundary")

            durable = client.ledger.load_terminal_evidence(request.execution_id)
            self.assertIsNotNone(durable)
            self.assertEqual(durable.terminal_class, AdapterTerminalState.UNKNOWN)

    async def test_command_preflight_crosses_real_process_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            command_dir = root / ".cursor" / "commands"
            command_dir.mkdir(parents=True)
            (command_dir / "opsx-bug-disposition.md").write_text(
                "contract_id: OPSX-WB-BUG-DISPOSITION-01\n",
                encoding="utf-8",
            )
            request = CursorExecutionRequest(
                execution_id="process-command-preflight-test",
                correlation_id="test",
                workspace=str(root),
                runtime=RuntimeKind.LOCAL,
                model_route=ModelRoute(model_id="composer-2.5", params={}),
                prompt="/opsx-bug-disposition",
                write_policy=WritePolicy.READ_ONLY,
                timeout_seconds=5,
            )
            client = CursorExecutorProcessClient(
                ledger_root=root / "ledger",
                timeout_grace_seconds=2,
            )
            result = await client.execute(request)

            self.assertEqual(result.terminal_class, AdapterTerminalState.BLOCKED)
            self.assertIsNotNone(result.error)
            assert result.error is not None
            self.assertEqual(result.error.class_name, "COMMAND_INPUT_REQUIRED")
            self.assertEqual(result.error.stage, "command_preflight")
            self.assertIn("STATUS: BLOCKED_NEEDS_INPUT", result.result_text or "")
            self.assertIsNone(result.agent_id)
            self.assertIsNone(result.run_id)

            durable = client.ledger.load_terminal_evidence(request.execution_id)
            self.assertIsNotNone(durable)
            assert durable is not None
            self.assertEqual(durable.terminal_class, AdapterTerminalState.BLOCKED)

    async def test_cancel_execution_kills_registered_subprocess_within_budget(self) -> None:
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            client = CursorExecutorProcessClient(
                ledger_root=Path(tmp),
                cleanup_budget_seconds=2.0,
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            execution_id = "cancel-registry-test"
            client._register_process(execution_id, proc, correlation_id="wf-1")
            cleaned = client.cancel_execution(execution_id, reason="test")
            self.assertTrue(cleaned)
            self.assertIsNotNone(proc.poll())

    async def test_cancel_bootstrap_executions_targets_suffix(self) -> None:
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            client = CursorExecutorProcessClient(ledger_root=Path(tmp))
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            eid = "wf-1:epoch:agent-bootstrap"
            client._register_process(eid, proc, correlation_id="wf-1")
            cancelled = client.cancel_bootstrap_executions("wf-1", suffix="agent-bootstrap")
            self.assertIn(eid, cancelled)

    def test_windows_agent_subprocess_env_strips_msys_and_sets_comspec(self) -> None:
        with patch("cursor_executor.bridge_local.os.name", "nt"), patch(
            "cursor_executor.bridge_local._default_windows_comspec",
            return_value=r"C:\Windows\System32\cmd.exe",
        ):
            env = windows_agent_subprocess_env(
                {
                    "MSYSTEM": "MINGW64",
                    "SHELL": r"C:\Program Files\Git\bin\bash.exe",
                    "PATH": "C:\\bin",
                }
            )
        self.assertEqual(env["COMSPEC"], r"C:\Windows\System32\cmd.exe")
        self.assertNotIn("MSYSTEM", env)
        self.assertNotIn("SHELL", env)
        self.assertEqual(env["PATH"], "C:\\bin")


if __name__ == "__main__":
    unittest.main()
