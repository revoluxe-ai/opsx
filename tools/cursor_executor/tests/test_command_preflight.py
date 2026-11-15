"""Deterministic slash-command preflight tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cursor_executor.command_preflight import validate_command_invocation
from cursor_executor.executor import CursorExecutor
from cursor_executor.ledger import LedgerStore
from cursor_executor.schemas import (
    AdapterTerminalState,
    CursorExecutionRequest,
    ModelRoute,
    RuntimeKind,
    WritePolicy,
)
from cursor_executor.sdk_client import SdkRunOutcome
from fake_sdk import FakeSdkClient


def _catalog() -> list[dict]:
    return [{"id": "composer-2.5", "displayName": "Composer", "parameters": []}]


class CommandPreflightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        command_dir = self.root / ".cursor" / "commands"
        command_dir.mkdir(parents=True)
        (command_dir / "opsx-bug-disposition.md").write_text(
            "contract_id: OPSX-WB-BUG-DISPOSITION-01\n", encoding="utf-8"
        )
        (command_dir / "opsx-bug-family-plan.md").write_text(
            "contract_id: OPSX-WB-BUG-FAMILY-PLAN-01\n", encoding="utf-8"
        )
        self.empty_input_commands = [
            "opsx-bug-intake-clear",
            "opsx-estimate",
            "opsx-intent-align",
            "opsx-meta-add-command",
            "opsx-meta-promote-process-lesson",
            "opsx-risk",
            "opsx-run-build",
            "opsx-run-tests",
            "opsx-run-typecheck",
            "opsx-security-review",
            "opsx-security-triage",
            "opsx-sherlock",
            "opsx-ts-design",
            "opsx-ts-review",
        ]
        for command_id in self.empty_input_commands:
            (command_dir / f"{command_id}.md").write_text(
                f"contract_id: TEST-{command_id}\n", encoding="utf-8"
            )
        self.ledger = LedgerStore(root=self.root / "ledger")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _request(self, prompt: str, *, execution_id: str = "preflight-1") -> CursorExecutionRequest:
        return CursorExecutionRequest(
            execution_id=execution_id,
            correlation_id="preflight-test",
            workspace=str(self.root),
            runtime=RuntimeKind.LOCAL,
            model_route=ModelRoute("composer-2.5", {}),
            prompt=prompt,
            write_policy=WritePolicy.READ_ONLY,
        )

    def test_disposition_missing_tokens_blocks_with_contract_identity(self) -> None:
        block = validate_command_invocation(self._request("/opsx-bug-disposition"))
        self.assertIsNotNone(block)
        assert block is not None
        self.assertEqual(block.code, "COMMAND_INPUT_REQUIRED")
        self.assertEqual(block.contract_status, "BLOCKED_NEEDS_INPUT")
        self.assertEqual(len(block.contract_sha256), 64)
        self.assertIn("STATUS: BLOCKED_NEEDS_INPUT", block.result_text)

    def test_disposition_go_blocks_before_execution(self) -> None:
        block = validate_command_invocation(
            self._request("/opsx-bug-disposition GO", execution_id="preflight-go")
        )
        self.assertIsNotNone(block)
        assert block is not None
        self.assertEqual(block.code, "COMMAND_GO_UNSUPPORTED")
        self.assertEqual(block.contract_status, "GO_UNSUPPORTED")

    def test_disposition_complete_identifiers_delegate(self) -> None:
        block = validate_command_invocation(
            self._request(
                "/opsx-bug-disposition change=C1 case=CASE-1 finding=F-1",
                execution_id="preflight-complete",
            )
        )
        self.assertIsNone(block)

    def test_family_plan_missing_clean_input_blocks(self) -> None:
        block = validate_command_invocation(
            self._request("/opsx-bug-family-plan", execution_id="family-empty")
        )
        self.assertIsNotNone(block)
        assert block is not None
        self.assertEqual(block.code, "COMMAND_INPUT_REQUIRED")
        self.assertEqual(block.contract_status, "BLOCKED_NEEDS_INPUT")

    def test_family_plan_explicit_finding_delegates(self) -> None:
        block = validate_command_invocation(
            self._request("/opsx-bug-family-plan finding=F-1", execution_id="family-one")
        )
        self.assertIsNone(block)

    def test_empty_argument_contracts_fail_closed_with_identity(self) -> None:
        for index, command_id in enumerate(self.empty_input_commands):
            with self.subTest(command_id=command_id):
                block = validate_command_invocation(
                    self._request(f"/{command_id}", execution_id=f"empty-{index}")
                )
                self.assertIsNotNone(block)
                assert block is not None
                self.assertEqual(len(block.contract_sha256), 64)
                self.assertTrue(block.contract_path.endswith(f"/{command_id}.md"))

    async def test_empty_argument_contracts_short_circuit_before_sdk(self) -> None:
        fake = FakeSdkClient(catalog=_catalog())
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        for index, command_id in enumerate(self.empty_input_commands):
            with self.subTest(command_id=command_id):
                result = await executor.execute(
                    self._request(f"/{command_id}", execution_id=f"empty-exec-{index}")
                )
                self.assertEqual(result.terminal_class, AdapterTerminalState.BLOCKED)
                self.assertIsNotNone(result.error)
                assert result.error is not None
                self.assertEqual(result.error.stage, "command_preflight")
        self.assertEqual(fake.list_calls, 0)
        self.assertEqual(fake.create_calls, 0)
        self.assertEqual(fake.send_calls, 0)

    def test_remaining_active_invalid_paths_fail_closed(self) -> None:
        cases = [
            ("opsx-doctor", "/opsx-doctor GO", "COMMAND_GO_UNSUPPORTED"),
            (
                "opsx-meta-update-all-snapshots",
                "/opsx-meta-update-all-snapshots GO",
                "SNAPSHOT_CENTRAL_OVERRIDE_MISSING",
            ),
            ("opsx-models", "/opsx-models scope=invalid", "COMMAND_SCOPE_INVALID"),
            ("opsx-plan", "/opsx-plan GO", "COMMAND_INPUT_REQUIRED"),
            ("opsx-rename-change", "/opsx-rename-change GO", "COMMAND_INPUT_REQUIRED"),
            (
                "opsx-meta-analyze-pipeline",
                "/opsx-meta-analyze-pipeline EXAMPLE_CHAT:",
                "EXAMPLE_CHAT_UNREADABLE",
            ),
        ]
        command_dir = self.root / ".cursor" / "commands"
        for command_id, prompt, expected_code in cases:
            path = command_dir / f"{command_id}.md"
            if not path.exists():
                path.write_text(f"contract_id: TEST-{command_id}\n", encoding="utf-8")
            with self.subTest(command_id=command_id):
                block = validate_command_invocation(
                    self._request(prompt, execution_id=f"invalid-{command_id}")
                )
                self.assertIsNotNone(block)
                assert block is not None
                self.assertEqual(block.code, expected_code)
                self.assertEqual(len(block.contract_sha256), 64)

    async def test_remaining_active_invalid_paths_short_circuit_before_sdk(self) -> None:
        fake = FakeSdkClient(catalog=_catalog())
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        command_dir = self.root / ".cursor" / "commands"
        cases = [
            ("opsx-doctor", "/opsx-doctor GO"),
            ("opsx-meta-update-all-snapshots", "/opsx-meta-update-all-snapshots GO"),
            ("opsx-models", "/opsx-models scope=invalid"),
            ("opsx-plan", "/opsx-plan GO"),
            ("opsx-rename-change", "/opsx-rename-change GO"),
            ("opsx-meta-analyze-pipeline", "/opsx-meta-analyze-pipeline EXAMPLE_CHAT:"),
        ]
        for command_id, prompt in cases:
            path = command_dir / f"{command_id}.md"
            if not path.exists():
                path.write_text(f"contract_id: TEST-{command_id}\n", encoding="utf-8")
            result = await executor.execute(
                self._request(prompt, execution_id=f"invalid-exec-{command_id}")
            )
            self.assertEqual(result.terminal_class, AdapterTerminalState.BLOCKED)
            self.assertIsNotNone(result.error)
            assert result.error is not None
            self.assertEqual(result.error.stage, "command_preflight")
        self.assertEqual(fake.list_calls, 0)
        self.assertEqual(fake.create_calls, 0)
        self.assertEqual(fake.send_calls, 0)

    async def test_executor_short_circuits_before_any_sdk_call(self) -> None:
        fake = FakeSdkClient(catalog=_catalog())
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        result = await executor.execute(
            self._request("/opsx-bug-disposition", execution_id="executor-block")
        )
        self.assertEqual(result.terminal_class, AdapterTerminalState.BLOCKED)
        self.assertIsNotNone(result.error)
        assert result.error is not None
        self.assertEqual(result.error.class_name, "COMMAND_INPUT_REQUIRED")
        self.assertEqual(result.error.stage, "command_preflight")
        self.assertIn("STATUS: BLOCKED_NEEDS_INPUT", result.result_text or "")
        self.assertEqual(fake.list_calls, 0)
        self.assertEqual(fake.create_calls, 0)
        self.assertEqual(fake.send_calls, 0)

    async def test_complete_invocation_reaches_sdk(self) -> None:
        fake = FakeSdkClient(catalog=_catalog())
        fake.next_run = SdkRunOutcome(
            run_id="run-ok",
            agent_id="agent-ok",
            sdk_status="finished",
            result_text="route checked",
            observed_route=ModelRoute("composer-2.5", {}),
            duration_ms=1,
        )
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        result = await executor.execute(
            self._request(
                "/opsx-bug-disposition change=C1 case=CASE-1 finding=F-1",
                execution_id="executor-delegate",
            )
        )
        self.assertEqual(result.terminal_class, AdapterTerminalState.SUCCEEDED)
        self.assertEqual(fake.list_calls, 1)
        self.assertEqual(fake.create_calls, 1)
        self.assertEqual(fake.send_calls, 1)


if __name__ == "__main__":
    unittest.main()
