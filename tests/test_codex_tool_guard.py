"""Codex app-server per-call OPSX authority tests, no model calls."""
from __future__ import annotations

import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opsx" / "opsx_runtime"))
from unittest.mock import patch

from opsx_cli.codex_app_server import CodexServerHost
from opsx_cli.codex_write_journal import CodexWriteJournal
from opsx_cli.codex_tool_gateway import (
    CodexToolDenied,
    CodexToolGateway,
    dynamic_tool_specs,
)


class CodexGatewayTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.repo = Path(d.name)
        (self.repo / "allowed").mkdir()
        (self.repo / "allowed" / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
        (self.repo / "private.txt").write_text("secret", encoding="utf-8")
        self.calls = []

        def guard(tool, args):
            self.calls.append((tool, dict(args)))
            p = args.get("path") or args.get("pattern") or ""
            if p.startswith("private") or p == "." or p.startswith(".."):
                raise ValueError("outside OPSX read scope")
        self.gateway = CodexToolGateway(
            self.repo,
            ["read", "ls", "glob", "grep"],
            guard=guard, read_scope=object(),
        )

    def test_direct_supervisor_adapter_caps_one_turn_without_expanding_budget(self):
        from agents.adapters.codex_app_server.codex_adapter import CodexAppServerAdapter
        self.assertEqual(CodexAppServerAdapter(timeout_seconds=10800).timeout_seconds, 3600)
        self.assertEqual(CodexAppServerAdapter(timeout_seconds=300).timeout_seconds, 300)
        for invalid in (0, -1, float("nan"), float("inf"), "3600"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    CodexAppServerAdapter(timeout_seconds=invalid)

    def test_missing_host_tool_evidence_fails_closed(self):
        with self.assertRaises(CodexToolDenied) as caught:
            self.gateway.require_tool_evidence()
        self.assertEqual(caught.exception.code, "CODEX_NO_TOOL_EVIDENCE")
        self.gateway.dispatch("read", {"path": "allowed/a.txt"})
        self.gateway.require_tool_evidence()

    def test_native_thread_turn_identity_callback(self):
        events = []
        host = CodexServerHost(
            self.gateway, identity_observer=lambda kind, value:
                events.append((kind, value)),
        )
        host.response_for({
            "method": "turn/started",
            "params": {"turn": {"id": "native-turn-123"}},
        })
        self.assertEqual(events, [("turn", "native-turn-123")])

    def test_read_allowed_calls_guard_before_io(self):
        self.assertEqual(self.gateway.dispatch("read", {"path": "allowed/a.txt"}), "alpha\nbeta\n")
        self.assertEqual(self.calls[0], ("read", {"path": "allowed/a.txt"}))

    def test_outside_scope_is_denied_before_contents(self):
        with self.assertRaises(CodexToolDenied) as caught:
            self.gateway.dispatch("read", {"path": "private.txt"})
        self.assertEqual(caught.exception.code, "CODEX_OPSX_TOOL_GUARD_DENIED")

    def test_absolute_and_traversal_paths_denied(self):
        for path in ("../private.txt", "/etc/passwd", r"C:\Users\somewhere\secret.txt"):
            with self.subTest(path=path):
                with self.assertRaises(CodexToolDenied):
                    self.gateway.dispatch("read", {"path": path})

    def test_native_write_not_authorized(self):
        for tool in ("shell", "write", "edit", "delete"):
            with self.subTest(tool=tool):
                with self.assertRaises(CodexToolDenied):
                    self.gateway.dispatch(tool, {"path": "allowed/a.txt"})
        self.assertEqual((self.repo / "allowed/a.txt").read_text(), "alpha\nbeta\n")

    def test_missing_guard_denied_before_launch(self):
        with self.assertRaises(CodexToolDenied) as caught:
            CodexToolGateway(self.repo, ["read"], guard=None, read_scope=object())
        self.assertEqual(caught.exception.code, "CODEX_OPSX_GUARD_REQUIRED")

    def test_unknown_tools_refused_at_specs(self):
        with self.assertRaises(CodexToolDenied):
            dynamic_tool_specs(["read", "shell"])
        with self.assertRaises(CodexToolDenied):
            dynamic_tool_specs(["read", "read"])

    def test_host_dispatch_calls_guard_and_returns_structured_result(self):
        host = CodexServerHost(self.gateway)
        host.thread_id = "t1"
        host.turn_id = "turn1"
        resp = host.response_for({
            "method": "item/tool/call", "id": 17,
            "params": {"threadId": "t1", "turnId": "turn1", "namespace": None,
                       "tool": "read", "arguments": {"path": "allowed/a.txt"}},
        })
        self.assertTrue(resp["result"]["success"])
        self.assertEqual(resp["id"], 17)
        self.assertIn("alpha", resp["result"]["contentItems"][0]["text"])
        self.assertEqual(self.calls[-1][0], "read")

    def test_authorized_missing_read_is_recoverable_in_same_turn(self):
        host = CodexServerHost(self.gateway)
        host.thread_id = "t1"
        host.turn_id = "turn1"

        def call(request_id, path):
            return host.response_for({
                "method": "item/tool/call", "id": request_id,
                "params": {
                    "threadId": "t1", "turnId": "turn1", "namespace": None,
                    "tool": "read", "arguments": {"path": path},
                },
            })

        failed = call(21, "allowed/test_<hash>.py")
        self.assertFalse(failed["result"]["success"])
        self.assertEqual(
            failed["result"]["contentItems"][0]["text"],
            "CODEX_FILE_NOT_READABLE",
        )
        self.assertFalse(host.failed)
        # A failed read alone must not count as accepted tool evidence.
        with self.assertRaisesRegex(CodexToolDenied, "CODEX_NO_TOOL_EVIDENCE"):
            self.gateway.require_tool_evidence()
        recovered = call(22, "allowed/a.txt")
        self.assertTrue(recovered["result"]["success"])
        self.assertFalse(host.failed)
        self.gateway.require_tool_evidence()

    def test_authorized_directory_read_is_recoverable_but_not_implicit_ls(self):
        host = CodexServerHost(self.gateway)
        host.thread_id = "t1"
        host.turn_id = "turn1"
        def call(request_id, path):
            return host.response_for({
                "method": "item/tool/call", "id": request_id,
                "params": {
                    "threadId": "t1", "turnId": "turn1",
                    "namespace": None, "tool": "read",
                    "arguments": {"path": path},
                },
            })
        denied = call(41, "allowed")
        self.assertFalse(denied["result"]["success"])
        self.assertEqual(
            denied["result"]["contentItems"][0]["text"],
            "CODEX_READ_REQUIRES_FILE",
        )
        self.assertFalse(host.failed)
        with self.assertRaisesRegex(CodexToolDenied, "CODEX_NO_TOOL_EVIDENCE"):
            self.gateway.require_tool_evidence()
        accepted = call(42, "allowed/a.txt")
        self.assertTrue(accepted["result"]["success"])
        self.assertFalse(host.failed)
        self.gateway.require_tool_evidence()

    def test_directory_read_outside_existing_scope_is_fatal(self):
        from pathlib import Path
        (self.repo / "private_dir").mkdir()
        host = CodexServerHost(self.gateway)
        host.thread_id = "t1"
        host.turn_id = "turn1"
        denied = host.response_for({
            "method": "item/tool/call", "id": 49,
            "params": {
                "threadId": "t1", "turnId": "turn1",
                "namespace": None, "tool": "read",
                "arguments": {"path": "private_dir"},
            },
        })
        self.assertFalse(denied["result"]["success"])
        self.assertEqual(
            denied["result"]["contentItems"][0]["text"],
            "CODEX_OPSX_TOOL_GUARD_DENIED",
        )
        self.assertTrue(host.failed)

    def test_missing_read_outside_scope_still_fails_closed(self):
        host = CodexServerHost(self.gateway)
        host.thread_id = "t1"
        host.turn_id = "turn1"
        response = host.response_for({
            "method": "item/tool/call", "id": 19,
            "params": {
                "threadId": "t1", "turnId": "turn1", "namespace": None,
                "tool": "read", "arguments": {"path": "private_missing.py"},
            },
        })
        self.assertFalse(response["result"]["success"])
        self.assertTrue(host.failed)
        self.assertEqual(
            response["result"]["contentItems"][0]["text"],
            "CODEX_OPSX_TOOL_GUARD_DENIED",
        )

    def test_host_denial_marks_entire_turn_failed(self):
        host = CodexServerHost(self.gateway)
        host.thread_id = "t1"
        host.turn_id = "turn1"
        resp = host.response_for({
            "method": "item/tool/call", "id": 22,
            "params": {"threadId": "t1", "turnId": "turn1", "namespace": None,
                       "tool": "read", "arguments": {"path": "private.txt"}},
        })
        self.assertFalse(resp["result"]["success"])
        self.assertTrue(host.failed)
        self.assertNotIn("secret", str(resp))

    def test_host_rejects_cross_turn_or_namespace(self):
        for overrides in ({"turnId": "wrong"}, {"namespace": "external"}):
            host = CodexServerHost(self.gateway)
            host.thread_id = "t1"
            host.turn_id = "turn1"
            params = {"threadId": "t1", "turnId": "turn1", "namespace": None,
                      "tool": "read", "arguments": {"path": "allowed/a.txt"}}
            params.update(overrides)
            r = host.response_for({"method": "item/tool/call", "id": 22, "params": params})
            self.assertFalse(r["result"]["success"])
            self.assertTrue(host.failed)

    def test_host_native_shell_and_patch_never_accepted(self):
        for native in ("commandExecution", "fileChange", "mcpToolCall"):
            host = CodexServerHost(self.gateway)
            host.response_for({"method": "item/started", "params": {"item": {"type": native}}})
            self.assertTrue(host.failed)

    def test_host_declines_approval(self):
        host = CodexServerHost(self.gateway)
        r = host.response_for({
            "method": "item/commandExecution/requestApproval", "id": 1, "params": {}
        })
        self.assertEqual(r["result"]["decision"], "decline")
        self.assertTrue(host.failed)

    def test_host_rejects_unknown_server_request(self):
        host = CodexServerHost(self.gateway)
        r = host.response_for({"method": "tool/surprise", "id": 5, "params": {}})
        self.assertEqual(r["error"]["code"], -32601)
        self.assertTrue(host.failed)

    def _edit_journal(self):
        jdir = tempfile.TemporaryDirectory()
        self.addCleanup(jdir.cleanup)
        journal = CodexWriteJournal(self.repo, path=Path(jdir.name) / "recovery.sqlite3")
        journal.__enter__()
        self.addCleanup(journal.__exit__, None, None, None)
        return journal

    def test_edit_uses_pre_and_post_opsx_guard(self):
        from types import SimpleNamespace
        events = []
        def edit_guard(tool, args, record=None):
            events.append((tool, record["status"] if record else None))
            self.assertEqual(args["path"], "allowed/a.txt")
        gateway = CodexToolGateway(
            self.repo, ["edit"],
            guard=edit_guard,
            read_scope=SimpleNamespace(restrict_write_allow=("allowed/a.txt",), deny=()),
            journal=self._edit_journal(),
        )
        output = gateway.dispatch("edit", {
            "path": "allowed/a.txt", "old_string": "alpha",
            "new_string": "gamma",
        })
        self.assertIn("EDIT_APPLIED", output)
        self.assertEqual((self.repo / "allowed/a.txt").read_text(), "gamma\nbeta\n")
        self.assertEqual(events, [("edit", "running"), ("edit", "completed")])
        gateway.confirm_edits()

    def test_edit_post_guard_rejects_and_rolls_back(self):
        from types import SimpleNamespace
        def reject_post(tool, args, record=None):
            if record and record["status"] == "completed":
                raise ValueError("failed after-write gate")
        gateway = CodexToolGateway(
            self.repo, ["edit"],
            guard=reject_post,
            read_scope=SimpleNamespace(restrict_write_allow=("allowed/a.txt",), deny=()),
            journal=self._edit_journal(),
        )
        with self.assertRaises(CodexToolDenied):
            gateway.dispatch("edit", {
                "path": "allowed/a.txt", "old_string": "alpha",
                "new_string": "gamma",
            })
        self.assertEqual((self.repo / "allowed/a.txt").read_text(), "alpha\nbeta\n")

    def test_edit_requires_write_allowlist(self):
        from agents.config import ReadScope
        from types import SimpleNamespace
        with self.assertRaises(CodexToolDenied) as caught:
            CodexToolGateway(
                self.repo, ["edit"], guard=lambda *args: None,
                read_scope=ReadScope(
                    allow=(), deny=(), restrict_allow=None,
                    restrict_write_allow=None,
                ),
            )
        self.assertEqual(caught.exception.code, "CODEX_WRITE_SCOPE_REQUIRED")

    def test_glob_skips_unauthorized_results(self):
        result = self.gateway.dispatch("glob", {"pattern": "*.txt"})
        self.assertNotIn("private.txt", result)


if __name__ == "__main__":
    unittest.main()
