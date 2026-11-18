"""Crash/restart, worktree and scope tests for Codex's declared edit tool."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opsx" / "opsx_runtime"))

from opsx_cli.codex_tool_gateway import CodexToolDenied, CodexToolGateway
from opsx_cli.codex_write_journal import CodexWriteJournal, WriteRecoveryError
from agents.adapters.codex_app_server.codex_adapter import _require_linked_worktree
from opsx_cli.codex_provider import CodexProviderError


class CodexWriteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.a = tempfile.TemporaryDirectory()
        self.addCleanup(self.a.cleanup)
        self.b = tempfile.TemporaryDirectory()
        self.addCleanup(self.b.cleanup)
        self.root = Path(self.a.name).resolve()
        self.path = self.root / "allowed" / "test.txt"
        self.path.parent.mkdir()
        self.path.write_bytes(b"BEFORE\n")
        self.store = Path(self.b.name) / "write.sqlite3"

    def _journal(self):
        return CodexWriteJournal(self.root, path=self.store)

    def _write(self, journal, old="BEFORE\n", new="AFTER\n"):
        journal.begin("allowed/test.txt", old.encode("utf-8"), new.encode("utf-8"))
        CodexToolGateway._atomic_replace(self.path, new.encode("utf-8"))

    def test_crash_after_write_recovered_before_new_turn(self):
        with self._journal() as j:
            self._write(j)
            self.assertEqual(j.pending_count(), 1)
        self.assertEqual(self.path.read_text(), "AFTER\n")
        with self._journal() as j:
            self.assertEqual(j.recover(CodexToolGateway._atomic_replace), 1)
            self.assertEqual(j.pending_count(), 0)
        self.assertEqual(self.path.read_text(), "BEFORE\n")

    def test_crash_before_os_replace_is_idempotently_recovered(self):
        with self._journal() as j:
            j.begin("allowed/test.txt", b"BEFORE\n", b"AFTER\n")
        with self._journal() as j:
            self.assertEqual(j.recover(CodexToolGateway._atomic_replace), 1)
            self.assertEqual(j.recover(CodexToolGateway._atomic_replace), 0)
        self.assertEqual(self.path.read_text(), "BEFORE\n")

    def test_two_edits_to_same_file_reverse_in_order(self):
        with self._journal() as j:
            self._write(j)
            self._write(j, "AFTER\n", "AFTER2\n")
            self.assertEqual(j.pending_count(), 2)
        with self._journal() as j:
            self.assertEqual(j.recover(CodexToolGateway._atomic_replace), 2)
        self.assertEqual(self.path.read_text(), "BEFORE\n")

    def test_completed_turn_is_not_rolled_back(self):
        with self._journal() as j:
            self._write(j)
            j.confirm()
        with self._journal() as j:
            self.assertEqual(j.recover(CodexToolGateway._atomic_replace), 0)
        self.assertEqual(self.path.read_text(), "AFTER\n")

    def test_foreign_mutation_pauses_and_preserves_bytes(self):
        with self._journal() as j:
            self._write(j)
        self.path.write_bytes(b"SOMEONE_ELSE\n")
        with self._journal() as j:
            with self.assertRaises(WriteRecoveryError) as caught:
                j.recover(CodexToolGateway._atomic_replace)
            self.assertEqual(caught.exception.code, "CODEX_RECOVERY_CONCURRENT_CHANGE")
            self.assertEqual(j.pending_count(), 1)
        self.assertEqual(self.path.read_text(), "SOMEONE_ELSE\n")

    def test_double_write_lease_fails_closed(self):
        with self._journal():
            with self.assertRaises(WriteRecoveryError) as caught:
                with self._journal():
                    pass
            self.assertEqual(caught.exception.code, "CODEX_WORKTREE_WRITE_LOCKED")

    def test_journal_cannot_live_inside_worktree(self):
        with self.assertRaises(WriteRecoveryError) as caught:
            CodexWriteJournal(self.root, path=self.root / ".opsx" / "w.sqlite3")
        self.assertEqual(caught.exception.code, "CODEX_JOURNAL_INSIDE_WORKTREE")

    def test_direct_edit_cannot_omit_journal(self):
        scope = SimpleNamespace(restrict_write_allow=("allowed/test.txt",), deny=())
        with self.assertRaises(CodexToolDenied) as caught:
            CodexToolGateway(
                self.root, ["edit"], guard=lambda *a: None, read_scope=scope,
            )
        self.assertEqual(caught.exception.code, "CODEX_WRITE_JOURNAL_REQUIRED")

    def test_explicit_write_allow_enforced_even_when_legacy_mode_off(self):
        scope = SimpleNamespace(restrict_write_allow=("some_other_file.txt",), deny=())
        with self._journal() as journal:
            gateway = CodexToolGateway(
                self.root, ["edit"], guard=lambda *a: None,
                read_scope=scope, journal=journal,
            )
            with patch.dict(os.environ, {"OPSX_READ_SCOPE_MODE": "off"}):
                with self.assertRaises(CodexToolDenied) as caught:
                    gateway.dispatch("edit", {
                        "path": "allowed/test.txt", "old_string": "BEFORE",
                        "new_string": "AFTER",
                    })
                self.assertEqual(caught.exception.code, "CODEX_WRITE_OUTSIDE_ALLOWLIST")
        self.assertEqual(self.path.read_text(), "BEFORE\n")

    def test_deny_overrides_write_allow(self):
        scope = SimpleNamespace(
            restrict_write_allow=("allowed/**",), deny=("allowed/test.txt",),
        )
        with self._journal() as journal:
            gateway = CodexToolGateway(
                self.root, ["edit"], guard=lambda *a: None,
                read_scope=scope, journal=journal,
            )
            with self.assertRaises(CodexToolDenied) as caught:
                gateway.dispatch("edit", {
                    "path": "allowed/test.txt", "old_string": "BEFORE",
                    "new_string": "AFTER",
                })
            self.assertEqual(caught.exception.code, "CODEX_WRITE_DENIED_PATH")

    def test_model_completion_without_edit_is_not_proof(self):
        scope = SimpleNamespace(restrict_write_allow=("allowed/test.txt",), deny=())
        with self._journal() as journal:
            gateway = CodexToolGateway(
                self.root, ["edit"], guard=lambda *_: None,
                read_scope=scope, journal=journal,
            )
            with self.assertRaises(CodexToolDenied) as caught:
                gateway.require_net_edit()
            self.assertEqual(caught.exception.code, "CODEX_NO_EDIT_EVIDENCE")
            self.assertEqual(self.path.read_bytes(), b"BEFORE\n")

    def test_edit_then_revert_cannot_claim_net_change(self):
        scope = SimpleNamespace(restrict_write_allow=("allowed/test.txt",), deny=())
        with self._journal() as journal:
            gateway = CodexToolGateway(
                self.root, ["edit"], guard=lambda *_: None,
                read_scope=scope, journal=journal,
            )
            gateway.dispatch("edit", {
                "path": "allowed/test.txt", "old_string": "BEFORE",
                "new_string": "AFTER",
            })
            gateway.dispatch("edit", {
                "path": "allowed/test.txt", "old_string": "AFTER",
                "new_string": "BEFORE",
            })
            with self.assertRaises(CodexToolDenied) as caught:
                gateway.require_net_edit()
            self.assertEqual(caught.exception.code, "CODEX_NO_NET_EDIT")
            gateway.rollback_edits()
            self.assertEqual(self.path.read_bytes(), b"BEFORE\n")

    def test_permitted_host_edit_proves_effective_write(self):
        scope = SimpleNamespace(restrict_write_allow=("allowed/test.txt",), deny=())
        with self._journal() as journal:
            gateway = CodexToolGateway(
                self.root, ["edit"], guard=lambda *_: None,
                read_scope=scope, journal=journal,
            )
            gateway.dispatch("edit", {
                "path": "allowed/test.txt", "old_string": "BEFORE",
                "new_string": "AFTER",
            })
            gateway.require_net_edit()
            gateway.confirm_edits()
            self.assertEqual(self.path.read_bytes(), b"AFTER\n")

    def test_create_new_file_is_durable_and_no_clobber(self):
        scope = SimpleNamespace(restrict_write_allow=("allowed/**",), deny=())
        target = self.root / "allowed/new_test.py"
        with self._journal() as journal:
            gateway = CodexToolGateway(
                self.root, ["edit"], guard=lambda *a: None,
                read_scope=scope, journal=journal,
            )
            receipt = gateway.dispatch("edit", {
                "path": "allowed/new_test.py", "old_string": "",
                "new_string": "def test_generated(): assert True\n",
            })
            self.assertIn("FILE_CREATED", receipt)
            gateway.require_net_edit()
            gateway.confirm_edits()
            self.assertEqual(journal.pending_count(), 0)
        self.assertTrue(target.is_file())
        self.assertIn("test_generated", target.read_text())
        with self._journal() as journal:
            gateway = CodexToolGateway(
                self.root, ["edit"], guard=lambda *a: None,
                read_scope=scope, journal=journal,
            )
            with self.assertRaises(CodexToolDenied) as caught:
                gateway.dispatch("edit", {
                    "path": "allowed/new_test.py", "old_string": "",
                    "new_string": "OVERWRITE",
                })
            self.assertEqual(caught.exception.code, "CODEX_CREATE_PRECONDITION_FAILED")
            self.assertIn("test_generated", target.read_text())

    def test_crash_after_create_restores_absence(self):
        target = self.root / "allowed/from_crash.txt"
        with self._journal() as journal:
            journal.begin_create("allowed/from_crash.txt", b"CREATED\n")
            CodexToolGateway._atomic_create(target, b"CREATED\n")
        self.assertTrue(target.is_file())
        with self._journal() as journal:
            self.assertEqual(
                journal.recover(CodexToolGateway._atomic_replace), 1,
            )
            self.assertEqual(journal.pending_count(), 0)
        self.assertFalse(target.exists())

    def test_created_foreign_modification_cannot_be_deleted_on_recovery(self):
        target = self.root / "allowed/foreign.txt"
        with self._journal() as journal:
            journal.begin_create("allowed/foreign.txt", b"EXPECTED")
            CodexToolGateway._atomic_create(target, b"EXPECTED")
        target.write_bytes(b"EXTERNAL_EDIT")
        with self._journal() as journal:
            with self.assertRaises(WriteRecoveryError) as caught:
                journal.recover(CodexToolGateway._atomic_replace)
            self.assertEqual(caught.exception.code, "CODEX_RECOVERY_CONCURRENT_CHANGE")
            self.assertEqual(journal.pending_count(), 1)
        self.assertEqual(target.read_bytes(), b"EXTERNAL_EDIT")

    def test_create_does_not_make_parent_directories(self):
        scope = SimpleNamespace(restrict_write_allow=("allowed/**",), deny=())
        with self._journal() as journal:
            gateway = CodexToolGateway(
                self.root, ["edit"], guard=lambda *a: None,
                read_scope=scope, journal=journal,
            )
            with self.assertRaises(CodexToolDenied) as caught:
                gateway.dispatch("edit", {
                    "path": "allowed/missing/nested.py",
                    "old_string": "", "new_string": "pass",
                })
            self.assertEqual(caught.exception.code, "CODEX_CREATE_PRECONDITION_FAILED")
            self.assertFalse((self.root / "allowed/missing").exists())

    def test_main_checkout_is_not_worktree_write_authority(self):
        with tempfile.TemporaryDirectory() as d:
            local = Path(d)
            subprocess.run(["git", "init", "-q"], cwd=local, check=True)
            with self.assertRaises(CodexProviderError) as caught:
                _require_linked_worktree(local.resolve())
            self.assertEqual(caught.exception.code, "CODEX_WRITE_REQUIRES_LINKED_WORKTREE")

    def test_wrong_journal_root_denied(self):
        with self._journal() as journal:
            other = self.root / "allowed"
            with self.assertRaises(CodexToolDenied) as caught:
                CodexToolGateway(
                    other, ["edit"],
                    guard=lambda *a: None,
                    read_scope=SimpleNamespace(restrict_write_allow=("*",)),
                    journal=journal,
                )
            self.assertEqual(caught.exception.code, "CODEX_WRITE_JOURNAL_ROOT_MISMATCH")


if __name__ == "__main__":
    unittest.main()
