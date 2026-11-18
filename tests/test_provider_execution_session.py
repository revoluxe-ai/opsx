"""Provider-swap acceptance: one execution port, provider-owned transport."""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "opsx/opsx_runtime"))

from agents.adapters.execution_session_port import open_execution_session


class ProviderExecutionSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workspace = Path(tmp.name).resolve()
        (self.workspace / "opsx").mkdir()
        # Codex journals are outside the worktree, but hermetic for tests.
        env_patch = patch.dict(
            os.environ, {"LOCALAPPDATA": str(self.workspace.parent / ("journal-" + self.workspace.name))},
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    async def test_codex_owns_session_without_cursor_bridge(self):
        from agents.adapters.codex_app_server.execution_session import (
            CodexExecutionSession,
        )
        from agents.adapters.codex_app_server.codex_adapter import (
            CodexAppServerAdapter,
        )

        with patch.dict(os.environ, {"OPSX_ADAPTER_MODULE":
                    "agents.adapters.codex_app_server"}):
            # Even if the Cursor factory is registered in this process, the
            # selected provider identity must resolve to the Codex session.
            session = open_execution_session(
                self.workspace, store_backend="sqlite", store_scope="verify_impl:fixture",
            )
        self.assertIsInstance(session, CodexExecutionSession)
        async with session:
            self.assertEqual(session.model_selection(), {"id": "gpt-6-luna"})
            self.assertIsInstance(
                session.review_adapter(timeout_seconds=300), CodexAppServerAdapter,
            )
            self.assertIsInstance(
                session.repair_adapter(timeout_seconds=300), CodexAppServerAdapter,
            )
            await session.reconcile(
                "fixture", self.workspace, execution_id="fixed-execution",
            )
        self.assertFalse(session._entered)

    async def test_codex_rejects_wrong_repository_identity(self):
        with patch.dict(os.environ, {"OPSX_ADAPTER_MODULE":
                    "agents.adapters.codex_app_server"}):
            session = open_execution_session(
                self.workspace, store_backend="sqlite", store_scope="verify_impl:fixture",
            )
        async with session:
            with tempfile.TemporaryDirectory() as other:
                with self.assertRaisesRegex(
                    RuntimeError, "EXECUTION_WORKSPACE_IDENTITY_MISMATCH",
                ):
                    await session.reconcile(
                        "fixture", Path(other), execution_id="fixed-execution",
                    )

    async def test_codex_parent_budget_is_not_invalid_single_turn_timeout(self):
        from agents.adapters.codex_app_server.execution_session import (
            CodexExecutionSession,
        )
        session=CodexExecutionSession(
            workspace=self.workspace,store_backend="sqlite",
            store_scope="verify_impl:fixture",
        )
        async with session:
            self.assertEqual(session.review_adapter(timeout_seconds=10800).timeout_seconds,3600)
            self.assertEqual(session.repair_adapter(timeout_seconds=2700).timeout_seconds,2700)
            for value in (0, -1, float("inf"),float("nan")):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        session.review_adapter(timeout_seconds=value)

    async def test_codex_reconcile_pauses_unknown_mutating_turn(self):
        from opsx_cli.codex_turn_ledger import CodexTurnLedger
        from agents.adapters.codex_app_server.execution_session import CodexExecutionSession
        with CodexTurnLedger(self.workspace) as store:
            operation = store.begin(
                execution_id="prior", change_id="fixture",
                agent_key="apply_implement", model="gpt-6-luna",
                tools=("read", "edit"), prompt="patch fixture",
                input_revision="a" * 64,
            )
            store.record_thread(operation, "thread-old")
            store.record_turn(operation, "turn-old")
        session = CodexExecutionSession(
            workspace=self.workspace, store_backend="sqlite",
            store_scope="verify_impl:fixture",
        )
        async with session:
            with self.assertRaisesRegex(
                RuntimeError, "CODEX_EXECUTION_OUTCOME_UNKNOWN",
            ):
                await session.reconcile(
                    "fixture", self.workspace, execution_id="new",
                )

    async def test_codex_reconcile_rejects_foreign_active_dispatch(self):
        from agents.adapters.codex_app_server.execution_session import CodexExecutionSession
        from workflows.implementation.implementation_dispatch_ledger import (
            dispatch_ledger_path,
        )
        import sqlite3
        from contextlib import closing
        path = dispatch_ledger_path("fixture", repo_root=self.workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as db:
            db.execute(
                "CREATE TABLE dispatch_hop (change_id TEXT, run_id TEXT, sdk_status TEXT)"
            )
            db.execute(
                "INSERT INTO dispatch_hop VALUES(?,?,?)",
                ("fixture", "cursor-run", "RUNNING"),
            )
            db.commit()
        session = CodexExecutionSession(
            workspace=self.workspace, store_backend="sqlite",
            store_scope="verify_impl:fixture",
        )
        async with session:
            with self.assertRaisesRegex(
                RuntimeError, "CODEX_CROSS_PROVIDER_ACTIVE_DISPATCH",
            ):
                await session.reconcile(
                    "fixture", self.workspace, execution_id="next-execution",
                )

    async def test_cursor_owns_bridge_and_durable_reconcile(self):
        from agents.adapters.cursor_sdk.execution_session import (
            CursorExecutionSession,
        )
        from workflows.implementation.adapter_run_reconcile import (
            AdapterRunDisposition,
        )
        row = SimpleNamespace(
            record=SimpleNamespace(run_id="r1"),
            disposition=AdapterRunDisposition.FINISHED,
            get_run_status="FINISHED",
        )
        report = SimpleNamespace(
            rows=[row], error=None, index_db=self.workspace / "index.db",
        )
        with patch.dict(os.environ, {"OPSX_ADAPTER_MODULE":
                    "agents.adapters.cursor_sdk"}):
            with patch(
                "agents.adapters.cursor_sdk.execution_session.CursorBridgeRuntime",
            ) as bridge_constructor:
                bridge = bridge_constructor.return_value
                bridge.__aenter__ = AsyncMock(return_value=bridge)
                bridge.__aexit__ = AsyncMock(return_value=None)
                session = open_execution_session(
                    self.workspace, store_backend="sqlite",
                    store_scope="verify_impl:fixture",
                )
                self.assertIsInstance(session, CursorExecutionSession)
                bridge_constructor.assert_called_once()
                with (
                    patch(
                        "workflows.implementation.adapter_run_reconcile."
                        "reconcile_verify_impl_adapter_runs",
                        new_callable=AsyncMock, return_value=report,
                    ) as reconcile,
                    patch(
                        "workflows.implementation.adapter_run_reconcile."
                        "store_reconcile_report",
                    ) as store,
                    patch(
                        "agents.adapters.cursor_sdk.execution_session."
                        "ImplementationDispatchLedger",
                    ) as ledger_ctor,
                    patch(
                        "agents.adapters.cursor_sdk.execution_session."
                        "dispatch_ledger_path",
                        return_value=self.workspace / "dispatch.json",
                    ),
                    patch(
                        "agents.adapters.cursor_sdk.execution_session.CursorSDKAdapter",
                    ) as review,
                    patch(
                        "agents.adapters.cursor_sdk.execution_session.RepairCursorSDKAdapter",
                    ) as repair,
                ):
                    async with session:
                        await session.reconcile(
                            "fixture", self.workspace, execution_id="fixed-execution",
                        )
                        session.review_adapter(timeout_seconds=600)
                        session.repair_adapter(timeout_seconds=600)
                    reconcile.assert_awaited_once_with(
                        "fixture", self.workspace, bridge,
                    )
                    store.assert_called_once_with("fixed-execution", report)
                    ledger_ctor.return_value.clear_terminal.assert_called_once_with(
                        change_id="fixture", run_id="r1", sdk_status="FINISHED",
                    )
                    self.assertIs(
                        review.call_args.kwargs["bridge_runtime"], bridge,
                    )
                    self.assertIs(
                        repair.call_args.kwargs["bridge_runtime"], bridge,
                    )
                    bridge.__aenter__.assert_awaited_once()
                    bridge.__aexit__.assert_awaited_once()
                self.assertFalse(session._entered)

    def test_factory_and_bridge_hooks_do_not_depend_on_import_order(self):
        import agents.adapters.codex_app_server as _codex  # noqa: F401
        import agents.adapters.cursor_sdk as _cursor  # noqa: F401
        from agents.adapters.provider_bootstrap import (
            ensure_adapter_factories_registered,
        )
        from agents.adapters.provider_registry import (
            build_review_adapter,
            build_repair_adapter,
            reconcile_hook,
            factories_registered,
        )
        from agents.adapters.codex_app_server.codex_adapter import (
            CodexAppServerAdapter,
        )
        from agents.adapters.cursor_sdk.cursor_sdk_adapter import (
            CursorSDKAdapter,
        )
        from agents.adapters.cursor_sdk.repair_cursor_sdk_adapter import (
            RepairCursorSDKAdapter,
        )
        with patch.dict(os.environ, {"OPSX_ADAPTER_MODULE":
                    "agents.adapters.codex_app_server"}):
            ensure_adapter_factories_registered()
            self.assertTrue(factories_registered())
            self.assertIsInstance(
                build_review_adapter(timeout_seconds=240), CodexAppServerAdapter,
            )
            self.assertIsInstance(
                build_repair_adapter(timeout_seconds=240), CodexAppServerAdapter,
            )
            self.assertIsNone(reconcile_hook("bridge_runtime_type"))
        with patch.dict(os.environ, {"OPSX_ADAPTER_MODULE":
                    "agents.adapters.cursor_sdk"}):
            ensure_adapter_factories_registered()
            self.assertTrue(factories_registered())
            self.assertIsInstance(
                build_review_adapter(timeout_seconds=240), CursorSDKAdapter,
            )
            self.assertIsInstance(
                build_repair_adapter(timeout_seconds=240), RepairCursorSDKAdapter,
            )
            self.assertIsNotNone(reconcile_hook("bridge_runtime_type"))

    async def test_cursor_reconcile_rejects_foreign_workspace_and_empty_id(self):
        from agents.adapters.cursor_sdk.execution_session import CursorExecutionSession
        with patch("agents.adapters.cursor_sdk.execution_session.CursorBridgeRuntime") as factory:
            bridge = factory.return_value
            bridge.__aenter__ = AsyncMock(return_value=bridge)
            bridge.__aexit__ = AsyncMock(return_value=None)
            session = CursorExecutionSession(
                workspace=self.workspace,
                store_backend="sqlite",
                store_scope="verify_impl:fixture",
            )
            async with session:
                with tempfile.TemporaryDirectory() as other:
                    with self.assertRaisesRegex(
                        RuntimeError, "EXECUTION_WORKSPACE_IDENTITY_MISMATCH",
                    ):
                        await session.reconcile(
                            "fixture", Path(other), execution_id="fixed",
                        )
                with self.assertRaisesRegex(
                    RuntimeError, "EXECUTION_IDENTITY_REQUIRED",
                ):
                    await session.reconcile(
                        "fixture", self.workspace, execution_id="",
                    )

    def test_unsupported_provider_does_not_fallback(self):
        with patch.dict(os.environ, {"OPSX_ADAPTER_MODULE": "bad.provider"}):
            with self.assertRaisesRegex(
                RuntimeError, "EXECUTION_PROVIDER_UNAUTHORIZED",
            ):
                open_execution_session(
                    self.workspace, store_backend="sqlite",
                    store_scope="verify_impl:fixture",
                )

    def test_parent_graph_has_no_bridge_or_sdk_reconcile_calls(self):
        source = (ROOT / "opsx/opsx_runtime/run_verify_impl.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("open_execution_session(", source)
        self.assertIn("await session.reconcile(", source)
        self.assertNotIn("open_bridge_runtime(", source)
        self.assertNotIn("bridge_runtime=bridge", source)
        self.assertNotIn("reconcile_verify_impl_adapter_runs(", source)
