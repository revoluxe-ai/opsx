"""Proof that Codex parent turn identities survive process boundaries."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from opsx_cli.codex_turn_ledger import (
    CodexTurnLedger, CodexTurnLedgerError, working_tree_revision,
)


def git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *args],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


class DurableCodexTurnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory(prefix="opsx-codex-ledger-")
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.root = root / "repo"
        self.root.mkdir()
        self.ledger_path = root / "ledger.sqlite3"
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "opsx@example.invalid")
        git(self.root, "config", "user.name", "OPSX")
        (self.root / "demo.txt").write_text("one\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "fixture")
        self.revision = working_tree_revision(self.root)

    def open_ledger(self) -> CodexTurnLedger:
        return CodexTurnLedger(self.root, path=self.ledger_path)

    def begin(self, ledger: CodexTurnLedger, **overrides: str) -> str:
        params = dict(
            execution_id="exec-1", change_id="fixture",
            agent_key="apply_implement", model="gpt-6-luna",
            tools=("read", "edit"), prompt="update demo",
            input_revision=self.revision,
        )
        params.update(overrides)
        return ledger.begin(**params)

    def test_unknown_turn_survives_crash_and_blocks_other_execution(self) -> None:
        with self.open_ledger() as first:
            operation = self.begin(first)
            first.record_thread(operation, "thread-1")
            first.record_turn(operation, "turn-1")
        with self.open_ledger() as recovered:
            with self.assertRaises(CodexTurnLedgerError) as caught:
                recovered.reconcile()
            self.assertEqual(caught.exception.code, "CODEX_EXECUTION_OUTCOME_UNKNOWN")
            self.assertIn("thread-1", str(caught.exception))
            with self.assertRaises(CodexTurnLedgerError):
                self.begin(recovered, execution_id="exec-2")

    def test_accepted_result_is_identity_bound_and_durable(self) -> None:
        with self.open_ledger() as store:
            op = self.begin(store)
            store.record_thread(op, "thread-1")
            store.record_turn(op, "turn-1")
            with self.assertRaises(CodexTurnLedgerError):
                store.complete(
                    op, thread_id="thread-forged", turn_id="turn-1",
                    response="done", calls=[], output_revision=self.revision,
                )
            store.complete(
                op, thread_id="thread-1", turn_id="turn-1",
                response="secret output",
                calls=[{"tool": "edit", "verdict": "ALLOWED"}],
                output_revision=self.revision,
            )
        with self.open_ledger() as reopened:
            self.assertEqual(reopened.reconcile(), 1)
            row = reopened.get(op)
            self.assertEqual(row["status"], "COMPLETED")
            self.assertEqual(row["execution_id"], "exec-1")
            self.assertEqual(row["model"], "gpt-6-luna")
            self.assertEqual(row["input_revision"], self.revision)
            self.assertEqual(row["thread_id"], "thread-1")
            self.assertEqual(row["turn_id"], "turn-1")
            self.assertEqual(row["output_revision"], self.revision)
            self.assertNotIn("secret output", repr(row))
            with self.assertRaises(CodexTurnLedgerError) as caught:
                self.begin(reopened)
            self.assertEqual(caught.exception.code, "CODEX_DUPLICATE_TURN_DISPATCH")

    def test_resume_requires_same_execution_model_tools_and_output_revision(self) -> None:
        with self.open_ledger() as db:
            op=self.begin(db)
            db.record_thread(op,"thread-1")
            db.record_turn(op,"turn-1")
            db.complete(
                op,thread_id="thread-1",turn_id="turn-1",
                response="confirmed",calls=[{"tool":"edit","verdict":"ALLOWED"}],
                output_revision=self.revision,
            )
            valid=dict(
                thread_id="thread-1",execution_id="exec-1",
                change_id="fixture",model="gpt-6-luna",
                tools=("read","edit"),current_revision=self.revision,
            )
            self.assertEqual(db.require_resume(**valid)["operation_id"],op)
            for field, value in (
                ("execution_id","other"),("change_id","other"),
                ("model","different"),("tools",("read",)),
                ("current_revision","b"*64),
            ):
                with self.subTest(field=field):
                    changed=dict(valid,**{field:value})
                    with self.assertRaises(CodexTurnLedgerError) as caught:
                        db.require_resume(**changed)
                    self.assertEqual(
                        caught.exception.code,
                        "CODEX_RESUME_AUTHORITY_OR_REVISION_MISMATCH",
                    )
            with self.assertRaises(CodexTurnLedgerError):
                db.require_resume(**dict(valid,thread_id="foreign"))

    def test_readonly_failure_can_be_proven_safe_but_write_unknown_cannot(self) -> None:
        with self.open_ledger() as store:
            op = self.begin(store, tools=("read",))
            store.failure(op, code="CODEX_APP_SERVER_TIMEOUT", safe=True)
            self.assertEqual(store.get(op)["status"], "FAILED_SAFE")
            self.assertEqual(store.reconcile(), 0)
            other = self.begin(store, execution_id="exec-2")
            store.failure(other, code="CODEX_STREAM_DROPPED", safe=False)
            with self.assertRaises(CodexTurnLedgerError):
                store.reconcile()

    def test_thread_turn_rebinding_and_stale_receipt_denied(self) -> None:
        with self.open_ledger() as store:
            op = self.begin(store)
            with self.assertRaises(CodexTurnLedgerError):
                store.record_turn(op, "turn-1")
            store.record_thread(op, "thread-1")
            store.record_thread(op, "thread-1")
            with self.assertRaises(CodexTurnLedgerError) as caught:
                store.record_thread(op, "thread-other")
            self.assertEqual(caught.exception.code, "CODEX_PROVIDER_IDENTITY_CONFLICT")
            store.record_turn(op, "turn-1")
            with self.assertRaises(CodexTurnLedgerError) as caught:
                store.record_turn(op, "turn-other")
            self.assertEqual(caught.exception.code, "CODEX_PROVIDER_IDENTITY_CONFLICT")

    def test_revision_changes_with_byte_changes_even_if_git_status_same(self) -> None:
        file = self.root / "demo.txt"
        file.write_text("alpha", encoding="utf-8")
        first = working_tree_revision(self.root)
        file.write_text("bravo", encoding="utf-8")
        second = working_tree_revision(self.root)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, self.revision)

    def test_concurrent_ledgers_enforce_one_in_flight_turn(self) -> None:
        with self.open_ledger() as a, self.open_ledger() as b:
            op = self.begin(a)
            with self.assertRaises(CodexTurnLedgerError) as caught:
                self.begin(b, execution_id="other")
            self.assertEqual(caught.exception.code, "CODEX_EXECUTION_OUTCOME_UNKNOWN")
            a.failure(op, code="BEFORE_DISPATCH", safe=True)
            self.assertTrue(self.begin(b, execution_id="other"))