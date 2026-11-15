"""Hermetic T1–T12: create-only SDK session bootstrap (no bootstrap prompt send)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cursor_executor.executor import BootstrapDigestConflictError, CursorExecutor
from cursor_executor.ledger import LedgerRecord, LedgerStore
from cursor_executor.schemas import (
    AdapterTerminalState,
    CursorExecutionRequest,
    CursorExecutionResult,
    LifecyclePhase,
    ModelRoute,
    RuntimeKind,
    WritePolicy,
    canonical_request_digest,
    utc_now_iso,
)
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


def _route() -> ModelRoute:
    return ModelRoute(
        model_id="gpt-5.6-sol",
        params={"context": "272k", "reasoning": "high", "fast": "false"},
    )


def _session_request(**overrides) -> CursorExecutionRequest:
    base = CursorExecutionRequest(
        execution_id="wf:epoch:agent-bootstrap",
        correlation_id="wf",
        workspace="/tmp/ws",
        runtime=RuntimeKind.LOCAL,
        model_route=_route(),
        prompt="",
        write_policy=WritePolicy.READ_ONLY,
        timeout_seconds=120.0,
    )
    for key, val in overrides.items():
        setattr(base, key, val)
    return base


class BootstrapCreateOnlyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = LedgerStore(root=Path(self.tmp.name) / "ledger")
        self.fake = FakeSdkClient(catalog=[_catalog_model()])
        self.executor = CursorExecutor(sdk=self.fake, ledger=self.ledger)
        self._prior_api_key = os.environ.get("CURSOR_API_KEY")
        os.environ["CURSOR_API_KEY"] = "cursor_test_key"

    def tearDown(self) -> None:
        self.tmp.cleanup()
        if self._prior_api_key is None:
            os.environ.pop("CURSOR_API_KEY", None)
        else:
            os.environ["CURSOR_API_KEY"] = self._prior_api_key

    async def test_t1_create_only_skips_send_and_wait(self) -> None:
        outcome = await self.executor.bootstrap_local_agent(
            _session_request(), execution_id="wf:epoch:agent-bootstrap"
        )
        self.assertEqual(self.fake.create_calls, 1)
        self.assertEqual(self.fake.send_calls, 0)
        self.assertTrue(outcome.agent_id.startswith("agent-fake-"))

    async def test_t2_ledger_created_not_prompt_terminal(self) -> None:
        await self.executor.bootstrap_local_agent(
            _session_request(), execution_id="wf:epoch:agent-bootstrap"
        )
        record = self.ledger.load_record("wf:epoch:agent-bootstrap")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.lifecycle_phase, LifecyclePhase.CREATED.value)
        self.assertIsNone(record.run_id)
        self.assertIsNone(self.ledger.load_terminal_evidence("wf:epoch:agent-bootstrap"))

    async def test_t3_idempotent_retry_same_agent(self) -> None:
        first = await self.executor.bootstrap_local_agent(
            _session_request(), execution_id="wf:epoch:agent-bootstrap"
        )
        second = await self.executor.bootstrap_local_agent(
            _session_request(), execution_id="wf:epoch:agent-bootstrap"
        )
        self.assertEqual(first.agent_id, second.agent_id)
        self.assertEqual(self.fake.create_calls, 1)

    async def test_t4_digest_conflict_fail_closed(self) -> None:
        req = _session_request()
        _, digest, _ = self.executor._bootstrap_request_digests(
            req, execution_id="wf:epoch:agent-bootstrap"
        )
        self.ledger.save_record(
            LedgerRecord(
                execution_id="wf:epoch:agent-bootstrap",
                request_digest="other-digest",
                workspace=req.workspace,
                lifecycle_phase=LifecyclePhase.PREPARING.value,
                adapter_terminal=None,
                agent_id=None,
                run_id=None,
                requested_route=req.model_route.to_json(),
                validated_route=None,
                observed_route=None,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            )
        )
        with self.assertRaises(BootstrapDigestConflictError):
            await self.executor.bootstrap_local_agent(
                req, execution_id="wf:epoch:agent-bootstrap"
            )

    async def test_t5_legacy_inflight_ledger_agent_id(self) -> None:
        legacy = CursorExecutionRequest(
            execution_id="wf:epoch:agent-bootstrap",
            correlation_id="wf",
            workspace="/tmp/ws",
            runtime=RuntimeKind.LOCAL,
            model_route=_route(),
            prompt="__cursor_sdk_session_bootstrap__",
            write_policy=WritePolicy.READ_ONLY,
        )
        legacy_digest = canonical_request_digest(legacy)
        self.ledger.save_record(
            LedgerRecord(
                execution_id="wf:epoch:agent-bootstrap",
                request_digest=legacy_digest,
                workspace=legacy.workspace,
                lifecycle_phase=LifecyclePhase.RUNNING.value,
                adapter_terminal=AdapterTerminalState.SUCCEEDED.value,
                agent_id="agent-legacy",
                run_id="run-legacy",
                requested_route=legacy.model_route.to_json(),
                validated_route=legacy.model_route.to_json(),
                observed_route=None,
                created_at=utc_now_iso(),
                updated_at=utc_now_iso(),
            )
        )
        outcome = await self.executor.bootstrap_local_agent(
            _session_request(), execution_id="wf:epoch:agent-bootstrap"
        )
        self.assertEqual(outcome.agent_id, "agent-legacy")
        self.assertEqual(self.fake.create_calls, 0)

    async def test_t6_timings_keys_present(self) -> None:
        outcome = await self.executor.bootstrap_local_agent(
            _session_request(), execution_id="wf:epoch:agent-bootstrap"
        )
        for key in (
            "catalog_ms",
            "preflight_ms",
            "agent_create_ms",
            "bridge_init_ms",
            "ledger_publish_ms",
            "total_bootstrap_ms",
        ):
            self.assertIn(key, outcome.timings_ms)

    async def test_t7_execute_prompt_after_create_only(self) -> None:
        from cursor_executor.sdk_client import SdkRunOutcome

        self.fake.next_run = SdkRunOutcome(
            run_id="run-1",
            agent_id="agent-fake-1",
            sdk_status="finished",
            result_text="hi",
            observed_route=_route(),
            duration_ms=1,
        )
        created = await self.executor.bootstrap_local_agent(
            _session_request(), execution_id="wf:epoch:agent-bootstrap"
        )
        prompt_req = CursorExecutionRequest(
            execution_id="wf:epoch:review:1",
            correlation_id="wf",
            workspace="/tmp/ws",
            runtime=RuntimeKind.LOCAL,
            model_route=_route(),
            prompt="first real prompt",
            write_policy=WritePolicy.READ_ONLY,
        )
        result = await self.executor.execute_on_existing_agent(
            prompt_req, agent_id=created.agent_id
        )
        self.assertEqual(result.terminal_class, AdapterTerminalState.SUCCEEDED)
        self.assertEqual(self.fake.send_calls, 1)


class BootstrapProcessWorkerTests(unittest.TestCase):
    def test_t8_process_worker_bootstrap_operation(self) -> None:
        import asyncio
        import tempfile
        from cursor_executor.process_worker import _run

        fake = FakeSdkClient(catalog=[_catalog_model()])

        with tempfile.TemporaryDirectory() as tmp:
            ledger_root = Path(tmp) / "ledger"
            req = _session_request()
            envelope = {
                "operation": "bootstrap_local_agent",
                "request": req.to_json(),
                "execution_id": "wf:epoch:agent-bootstrap",
                "ledger_root": str(ledger_root),
            }
            os.environ["CURSOR_API_KEY"] = "cursor_test_key"

            original_init = CursorExecutor.__init__

            def _init_with_fake(self, *, sdk=None, ledger=None):
                original_init(self, sdk=fake, ledger=ledger)

            with patch.object(CursorExecutor, "__init__", _init_with_fake):
                payload = asyncio.run(_run(envelope))
            self.assertEqual(payload["kind"], "result")
            self.assertIn("bootstrap", payload)
            self.assertTrue(payload["bootstrap"]["agent_id"])


if __name__ == "__main__":
    unittest.main()
