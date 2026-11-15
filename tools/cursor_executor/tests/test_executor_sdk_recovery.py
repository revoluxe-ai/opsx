"""Hermetic tests for SDK run_id recovery, reconcile, and duplicate-send gates."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from cursor_executor.executor import CursorExecutor
from cursor_executor.ledger import (
    LedgerRecord,
    LedgerStore,
    is_idempotent_terminal_evidence,
)
from cursor_executor.schemas import (
    AdapterTerminalState,
    CursorExecutionRequest,
    LifecyclePhase,
    ModelRoute,
    ReconciliationState,
    RuntimeKind,
    WritePolicy,
    canonical_request_digest,
    utc_now_iso,
)
from cursor_executor.sdk_client import SdkRunOutcome
from fake_sdk import FakeSdkClient


def _catalog_model() -> dict:
    return {
        "id": "gpt-5.6-sol",
        "displayName": "GPT",
        "parameters": [
            {"id": "context", "values": [{"value": "272k"}]},
            {"id": "reasoning", "values": [{"value": "high"}, {"value": "low"}]},
            {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
        ],
    }


def _request(**overrides) -> CursorExecutionRequest:
    base = CursorExecutionRequest(
        execution_id="exec-recovery",
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


class ExecutorSdkRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = LedgerStore(root=Path(self.tmp.name) / "ledger")
        self._prior_api_key = os.environ.get("CURSOR_API_KEY")
        os.environ["CURSOR_API_KEY"] = "cursor_test_key"

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self._prior_api_key is None:
            os.environ.pop("CURSOR_API_KEY", None)
        else:
            os.environ["CURSOR_API_KEY"] = self._prior_api_key

    async def test_run_id_persisted_before_wait_completes(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        fake.next_run = SdkRunOutcome(
            run_id="run-early",
            agent_id="agent-1",
            sdk_status="finished",
            result_text="hello",
            observed_route=_request().model_route,
            duration_ms=1,
        )
        fake.timeout_on_send = True
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        result = await executor.execute(_request())
        self.assertEqual(result.terminal_class, AdapterTerminalState.UNKNOWN)
        stored = self.ledger.load_record("exec-recovery")
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertEqual(stored.run_id, "run-early")
        self.assertIsNone(self.ledger.load_terminal_evidence("exec-recovery"))

    async def test_reconcile_recovers_run_id_via_lookup(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        fake.runs["run-orphan"] = SdkRunOutcome(
            run_id="run-orphan",
            agent_id="agent-orphan",
            sdk_status="finished",
            result_text="hello",
            observed_route=_request().model_route,
            duration_ms=2,
        )
        fake.run_order.append("run-orphan")
        now = utc_now_iso()
        digest = canonical_request_digest(_request())
        record = LedgerRecord(
            execution_id="exec-recovery",
            request_digest=digest,
            workspace="/tmp/ws",
            lifecycle_phase=LifecyclePhase.RUNNING.value,
            adapter_terminal=None,
            agent_id="agent-orphan",
            run_id=None,
            requested_route=_request().model_route.to_json(),
            validated_route=_request().model_route.to_json(),
            observed_route=None,
            created_at=now,
            updated_at=now,
        )
        self.ledger.save_record(record)
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        recovered = await executor.reconcile("exec-recovery")
        self.assertEqual(recovered.terminal_class, AdapterTerminalState.SUCCEEDED)
        self.assertEqual(recovered.run_id, "run-orphan")
        self.assertEqual(recovered.reconciliation_state, ReconciliationState.TERMINAL_FROM_SDK)

    async def test_duplicate_send_without_run_id_reconciles_not_resends(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        fake.runs["run-dup"] = SdkRunOutcome(
            run_id="run-dup",
            agent_id="agent-dup",
            sdk_status="finished",
            result_text="hello",
            observed_route=_request().model_route,
            duration_ms=1,
        )
        fake.run_order.append("run-dup")
        now = utc_now_iso()
        digest = canonical_request_digest(_request())
        record = LedgerRecord(
            execution_id="exec-recovery",
            request_digest=digest,
            workspace="/tmp/ws",
            lifecycle_phase=LifecyclePhase.RUNNING.value,
            adapter_terminal=None,
            agent_id="agent-dup",
            run_id=None,
            requested_route=_request().model_route.to_json(),
            validated_route=_request().model_route.to_json(),
            observed_route=None,
            created_at=now,
            updated_at=now,
        )
        self.ledger.save_record(record)
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        second = await executor.execute_on_existing_agent(
            _request(),
            agent_id="agent-dup",
        )
        self.assertEqual(fake.send_calls, 0)
        self.assertEqual(second.terminal_class, AdapterTerminalState.SUCCEEDED)

    async def test_unknown_evidence_not_definitive_terminal(self) -> None:
        from cursor_executor.schemas import AuthSource, CursorExecutionResult

        unknown = CursorExecutionResult(
            execution_id="x",
            correlation_id=None,
            workspace=".",
            runtime=RuntimeKind.LOCAL,
            requested_model_route=ModelRoute("m", {}),
            validated_model_route=None,
            observed_model_route=None,
            model_match=None,
            agent_id="a",
            run_id="r",
            sdk_status=None,
            adapter_state=AdapterTerminalState.UNKNOWN,
            lifecycle_phase=LifecyclePhase.RUNNING,
            terminal_class=AdapterTerminalState.UNKNOWN,
            prompt_digest_sha256="abc",
            write_policy=WritePolicy.READ_ONLY,
            write_policy_enforcement="READ_ONLY_BUILTIN_ALLOWLIST",
            auth_source=AuthSource.PROCESS_ENV,
            process_env_key_present=False,
            user_env_key_present=False,
            created_at=utc_now_iso(),
            started_at=None,
            finished_at=utc_now_iso(),
            duration_ms=None,
            reconciliation_state=ReconciliationState.INDETERMINATE,
            result_text=None,
            error=None,
        )
        self.assertFalse(is_idempotent_terminal_evidence(unknown))

    async def test_reconcile_remote_protocol_error_is_read_only_pause(self) -> None:
        fake = FakeSdkClient(catalog=[_catalog_model()])
        fake.reconcile_read_error = True
        now = utc_now_iso()
        digest = canonical_request_digest(_request())
        record = LedgerRecord(
            execution_id="exec-recovery",
            request_digest=digest,
            workspace="/tmp/ws",
            lifecycle_phase=LifecyclePhase.RUNNING.value,
            adapter_terminal=None,
            agent_id="agent-x",
            run_id="run-x",
            requested_route=_request().model_route.to_json(),
            validated_route=_request().model_route.to_json(),
            observed_route=None,
            created_at=now,
            updated_at=now,
        )
        self.ledger.save_record(record)
        executor = CursorExecutor(sdk=fake, ledger=self.ledger)
        result = await executor.reconcile("exec-recovery")
        self.assertEqual(result.error.class_name, "EXECUTION_PAUSE")
        self.assertIsNone(self.ledger.load_terminal_evidence("exec-recovery"))


if __name__ == "__main__":
    unittest.main()
