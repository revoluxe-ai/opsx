"""LangGraph Codex provider dispatch and checkpoint identity tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opsx_cli.codex_app_server import CodexGuardedLangGraphAdapter
from tools.langgraph_runtime.backend import RealVerificationBackend
from tools.langgraph_runtime.cli import ensure_resume_provider_identity


class LangGraphCodexTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)

    def test_codex_provider_selected_without_cursor_sdk(self):
        with patch("tools.langgraph_runtime.backend.CodexGuardedLangGraphAdapter") as factory:
            backend = RealVerificationBackend(self.repo, run_id="r1", provider="codex")
            result = backend._adapter()
        factory.assert_called_once_with(self.repo, model="gpt-6-luna")
        self.assertIs(result, factory.return_value)

    def test_codex_executor_denied_before_any_provider_dispatch(self):
        assignment = {
            "bounded_verify_target": True,
            "change_id": "fixture",
            "plan_id": "plan-1",
            "required_check_id": "check-1",
            "assignment_epoch": 1,
            "ledger_revision": 1,
            "executor": "bounded-check",
            "expected_result_path": "evidence/out.json",
        }
        backend = RealVerificationBackend(self.repo, run_id="r1", provider="codex")
        with patch.object(backend, "_adapter") as adapter:
            result = backend.execute_assigned("fixture", assignment)
        self.assertEqual(result["started"], False)
        self.assertEqual(result["completed"], False)
        self.assertEqual(result["uncertain"], False)
        self.assertEqual(result["durable_result_found"], False)
        self.assertIn("CODEX_WRITE_AUTHORITY_UNPROVEN", result["raw_output"])
        adapter.assert_not_called()

    def test_provider_resume_identity_mismatch_fails_closed(self):
        checkpoint = SimpleNamespace(values={"provider": "cursor", "model_id": None})
        with self.assertRaisesRegex(RuntimeError, "PROVIDER_IDENTITY_MISMATCH"):
            ensure_resume_provider_identity(
                checkpoint, provider="codex", codex_model="gpt-6-luna"
            )

    def test_codex_model_resume_mismatch_fails_closed(self):
        checkpoint = SimpleNamespace(values={"provider": "codex", "model_id": "gpt-6-luna"})
        with self.assertRaisesRegex(RuntimeError, "MODEL_IDENTITY_MISMATCH"):
            ensure_resume_provider_identity(
                checkpoint, provider="codex", codex_model="gpt-6.1-terra"
            )

    def test_exact_provider_model_resume_accepted(self):
        checkpoint = SimpleNamespace(values={"provider": "codex", "model_id": "gpt-6-luna"})
        ensure_resume_provider_identity(
            checkpoint, provider="codex", codex_model="gpt-6-luna"
        )

    def test_legacy_cursor_checkpoint_keeps_compatibility(self):
        checkpoint = SimpleNamespace(values={"change": "legacy"})
        ensure_resume_provider_identity(
            checkpoint, provider="cursor", codex_model="gpt-6-luna"
        )
        with self.assertRaisesRegex(RuntimeError, "PROVIDER_IDENTITY_MISMATCH"):
            ensure_resume_provider_identity(
                checkpoint, provider="codex", codex_model="gpt-6-luna"
            )

    def test_reject_unknown_provider(self):
        with self.assertRaisesRegex(ValueError, "Unknown execution provider"):
            RealVerificationBackend(self.repo, run_id="r1", provider="surprise")


    def test_graph_checkpoint_persists_codex_identity(self):
        from langgraph.checkpoint.sqlite import SqliteSaver
        from tools.langgraph_runtime.journal import DispatchJournal
        from tools.langgraph_runtime.verification import build_verification_graph
        from tools.langgraph_runtime.test_verification import FakeBackend, passed

        with SqliteSaver.from_conn_string(str(self.repo / "checkpoints.sqlite3")) as saver:
            saver.setup()
            graph = build_verification_graph(
                FakeBackend([passed()]),
                DispatchJournal(self.repo / "dispatch.sqlite3"),
                repo=self.repo,
                checkpointer=saver,
            )
            cfg = {"configurable": {"thread_id": "run-codex"}}
            graph.invoke(
                {"run_id": "run-codex", "change": "fixture",
                 "provider": "codex", "model_id": "gpt-6-luna"},
                config=cfg,
                durability="sync",
            )
            checkpoint = graph.get_state(cfg)
            self.assertEqual(checkpoint.values["provider"], "codex")
            self.assertEqual(checkpoint.values["model_id"], "gpt-6-luna")


if __name__ == "__main__":
    unittest.main()
