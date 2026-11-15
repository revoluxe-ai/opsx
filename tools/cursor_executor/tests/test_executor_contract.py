"""Contract tests for cursor_executor adapter V0.1."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cursor_executor.auth import resolve_auth
from cursor_executor.executor import CursorExecutor
from cursor_executor.ledger import LedgerStore
from cursor_executor.preflight import validate_model_route
from cursor_executor.schemas import (
    AdapterTerminalState,
    CursorExecutionRequest,
    ModelRoute,
    ReconciliationState,
    RuntimeKind,
    WritePolicy,
    model_routes_equal,
)
from fake_sdk import FakeSdkClient


def _catalog_model() -> dict:
    return {
        "id": "gpt-5.6-sol",
        "displayName": "GPT",
        "parameters": [
            {
                "id": "context",
                "values": [{"value": "272k"}, {"value": "128k"}],
            },
            {
                "id": "reasoning",
                "values": [{"value": "high"}, {"value": "low"}],
            },
            {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
        ],
    }


def _request(**overrides) -> CursorExecutionRequest:
    base = CursorExecutionRequest(
        execution_id="exec-1",
        workspace="/tmp/ws",
        runtime=RuntimeKind.LOCAL,
        model_route=ModelRoute(
            model_id="gpt-5.6-sol",
            params={"context": "272k", "reasoning": "high", "fast": "false"},
        ),
        prompt="hello",
        write_policy=WritePolicy.READ_ONLY,
    )
    for key, val in overrides.items():
        setattr(base, key, val)
    return base


class ExecutorContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = LedgerStore(root=Path(self.tmp.name) / "ledger")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    async def _run(self, fake: FakeSdkClient, request: CursorExecutionRequest | None = None):
        request = request or _request()
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        return await executor.execute(request)

    def test_preflight_accepts_exact_route(self) -> None:
        from cursor_executor.preflight import PreflightSuccess

        ok = validate_model_route(_request().model_route, [_catalog_model()])
        self.assertIsInstance(ok, PreflightSuccess)
        self.assertEqual(ok.validated_route.model_id, "gpt-5.6-sol")

    def test_unknown_model_rejected(self) -> None:
        fail = validate_model_route(
            ModelRoute("missing-model", {}),
            [_catalog_model()],
        )
        self.assertEqual(fail.code, "MODEL_ROUTE_UNAVAILABLE")

    def test_unknown_parameter_rejected(self) -> None:
        route = ModelRoute("gpt-5.6-sol", {"bogus": "x"})
        fail = validate_model_route(route, [_catalog_model()])
        self.assertEqual(fail.code, "MODEL_ROUTE_UNAVAILABLE")

    def test_unavailable_value_rejected(self) -> None:
        route = ModelRoute("gpt-5.6-sol", {"context": "999k", "reasoning": "high", "fast": "false"})
        fail = validate_model_route(route, [_catalog_model()])
        self.assertEqual(fail.code, "MODEL_ROUTE_UNAVAILABLE")

    async def test_finished_exact_route_success(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="finished",
            result_text="hello",
            observed_route=_request().model_route,
            duration_ms=10,
        )
        result = await self._run(fake)
        self.assertEqual(result.terminal_class, AdapterTerminalState.SUCCEEDED)
        self.assertTrue(result.model_match)

    async def test_model_mismatch_non_success(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="finished",
            result_text="hello",
            observed_route=ModelRoute("other-model", {}),
            duration_ms=10,
        )
        result = await self._run(fake)
        self.assertNotEqual(result.terminal_class, AdapterTerminalState.SUCCEEDED)
        self.assertEqual(result.error.class_name, "MODEL_ROUTE_MISMATCH")

    async def test_param_mismatch_non_success(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="finished",
            result_text="hello",
            observed_route=ModelRoute(
                "gpt-5.6-sol",
                {"context": "128k", "reasoning": "high", "fast": "false"},
            ),
            duration_ms=10,
        )
        result = await self._run(fake)
        self.assertEqual(result.terminal_class, AdapterTerminalState.FAILED)

    async def test_sdk_error_failed(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="error",
            result_text="",
            observed_route=_request().model_route,
            duration_ms=1,
        )
        result = await self._run(fake)
        self.assertEqual(result.terminal_class, AdapterTerminalState.FAILED)

    async def test_cancelled(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="cancelled",
            result_text="",
            observed_route=_request().model_route,
            duration_ms=1,
        )
        result = await self._run(fake)
        self.assertEqual(result.terminal_class, AdapterTerminalState.CANCELLED)

    async def test_expired_failed(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="expired",
            result_text="",
            observed_route=_request().model_route,
            duration_ms=1,
        )
        result = await self._run(fake)
        self.assertEqual(result.terminal_class, AdapterTerminalState.FAILED)

    async def test_timeout_unknown(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        fake.timeout_on_send = True
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="finished",
            result_text="",
            observed_route=_request().model_route,
            duration_ms=1,
        )
        result = await self._run(fake)
        self.assertEqual(result.terminal_class, AdapterTerminalState.UNKNOWN)

    async def test_transport_unknown(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        fake.transport_error = True
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="finished",
            result_text="",
            observed_route=_request().model_route,
            duration_ms=1,
        )
        result = await self._run(fake)
        self.assertEqual(result.terminal_class, AdapterTerminalState.UNKNOWN)

    async def test_unknown_never_success(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        fake.timeout_on_send = True
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="running",
            result_text="",
            observed_route=_request().model_route,
            duration_ms=1,
        )
        result = await self._run(fake)
        self.assertNotEqual(result.terminal_class, AdapterTerminalState.SUCCEEDED)

    async def test_duplicate_execution_reconciles(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-dup",
            agent_id="agent-dup",
            sdk_status="finished",
            result_text="hello",
            observed_route=_request().model_route,
            duration_ms=3,
        )
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        first = await executor.execute(_request())
        self.assertEqual(fake.create_calls, 1)
        second = await executor.execute(_request())
        self.assertEqual(fake.create_calls, 1)
        self.assertEqual(second.reconciliation_state, ReconciliationState.TERMINAL_FROM_LEDGER)
        self.assertEqual(first.run_id, second.run_id)

    async def test_execution_id_conflict(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="finished",
            result_text="hello",
            observed_route=_request().model_route,
            duration_ms=1,
        )
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        await executor.execute(_request())
        conflict = await executor.execute(_request(execution_id="exec-1", prompt="different"))
        self.assertEqual(conflict.error.class_name, "EXECUTION_ID_CONFLICT")

    async def test_reconcile_terminal_idempotent(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-rec",
            agent_id="agent-rec",
            sdk_status="finished",
            result_text="hello",
            observed_route=_request().model_route,
            duration_ms=2,
        )
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        executed = await executor.execute(_request(execution_id="exec-rec"))
        fake.create_calls = 0
        fake.send_calls = 0
        other = CursorExecutor(sdk=fake, ledger=self.ledger)
        recovered = await other.reconcile("exec-rec")
        self.assertEqual(recovered.run_id, executed.run_id)
        self.assertEqual(fake.create_calls, 0)
        self.assertEqual(fake.send_calls, 0)
        self.assertEqual(recovered.reconciliation_state, ReconciliationState.TERMINAL_FROM_LEDGER)

    def test_corrupt_evidence_not_pass(self) -> None:
        store = self.ledger
        path = store._evidence_path("exec-corrupt")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"execution_id": "exec-corrupt"', encoding="utf-8")
        self.assertTrue(store.is_corrupt_evidence_file("exec-corrupt"))
        self.assertIsNone(store.load_terminal_evidence("exec-corrupt"))

    def test_auth_does_not_leak_secret(self) -> None:
        os.environ["CURSOR_API_KEY"] = "cursor_test_secret_value"
        try:
            auth = resolve_auth()
            blob = json.dumps(
                {
                    "source": auth.source.value,
                    "present": bool(auth.api_key),
                }
            )
            self.assertNotIn("cursor_test_secret_value", blob)
        finally:
            del os.environ["CURSOR_API_KEY"]

    def test_auth_prefers_hkcu_in_cursor_agent_terminal(self) -> None:
        if sys.platform != "win32":
            self.skipTest("HKCU auth path is Windows-only")
        os.environ["CURSOR_API_KEY"] = "cursor_injected_by_ide"
        os.environ["CURSOR_AGENT"] = "1"
        try:
            with mock.patch(
                "cursor_executor.auth._read_windows_user_env",
                return_value="cursor_owner_user_key",
            ):
                from cursor_executor.schemas import AuthSource

                auth = resolve_auth()
            self.assertEqual(auth.source, AuthSource.WINDOWS_USER_ENV)
            self.assertEqual(auth.api_key, "cursor_owner_user_key")
            self.assertTrue(auth.process_env_key_present)
        finally:
            del os.environ["CURSOR_API_KEY"]
            del os.environ["CURSOR_AGENT"]

    def test_result_serialization_no_secret(self) -> None:
        os.environ["CURSOR_API_KEY"] = "cursor_test_secret_value"
        try:
            from cursor_executor.schemas import AuthSource

            sample = {
                "auth_source": AuthSource.PROCESS_ENV.value,
                "process_env_key_present": True,
            }
            blob = json.dumps(sample)
            self.assertNotIn("cursor_test_secret_value", blob)
        finally:
            del os.environ["CURSOR_API_KEY"]

    def test_param_ordering_equality(self) -> None:
        a = ModelRoute("m", {"b": "2", "a": "1"})
        b = ModelRoute("m", {"a": "1", "b": "2"})
        self.assertTrue(model_routes_equal(a, b))

    async def test_sdk_error_includes_status_message(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-1",
            sdk_status="error",
            result_text="",
            observed_route=_request().model_route,
            duration_ms=1,
            sdk_status_message="[internal] unable to open database file",
        )
        result = await self._run(fake)
        self.assertEqual(result.error.class_name, "SDK_ERROR")
        self.assertIn("unable to open database file", result.error.message)

    async def test_read_only_tools_empty_on_create(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        from cursor_executor.sdk_client import SdkRunOutcome

        fake.next_run = SdkRunOutcome(
            run_id="run-ro",
            agent_id="agent-ro",
            sdk_status="finished",
            result_text="x",
            observed_route=_request().model_route,
            duration_ms=1,
        )
        await self._run(fake)
        self.assertEqual(fake.create_calls, 1)


if __name__ == "__main__":
    unittest.main()
