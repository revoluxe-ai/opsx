"""Codex supervisor bridge: registered bounded tools and execution identity."""
from __future__ import annotations
import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "opsx" / "opsx_runtime"))

from opsx_cli.codex_tool_gateway import CodexToolGateway, CodexToolDenied
from opsx_cli.codex_app_server import CodexServerHost
from opsx_cli.codex_provider import CodexProviderError
from agents.adapters.codex_app_server.codex_adapter import _require_linked_worktree
from agents.agent_tools.deterministic.opsx_pytest_tool import build_run_opsx_pytest_custom_tool


class FakeBoundedCheck:
    def __init__(self):
        self.calls = []

    async def execute(self, args, _ctx):
        self.calls.append(dict(args))
        return {"ok": True, "exit_code": 0, "stdout_tail": "one node passed"}


class CodexBoundedToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        self.spec = FakeBoundedCheck()
        self.guard_calls = []
        self.gateway = CodexToolGateway(
            self.repo, ["run_pytest"],
            guard=lambda tool, args: self.guard_calls.append((tool, dict(args))),
            read_scope=object(), registered_tools={"run_pytest": self.spec},
        )

    async def test_registered_pytest_no_shell(self):
        result = await self.gateway.dispatch_async(
            "run_pytest", {"node_id": "tests/test_unit.py::test_one"}
        )
        self.assertEqual(json.loads(result)["ok"], True)
        self.assertEqual(self.spec.calls, [{"node_id": "tests/test_unit.py::test_one"}])
        self.assertEqual(self.guard_calls[0][0], "run_pytest")

    async def test_unknown_or_unbound_tools_fail_before_execution(self):
        with self.assertRaises(CodexToolDenied):
            CodexToolGateway(
                self.repo, ["run_pytest"], guard=lambda *_: None,
                read_scope=object(),
            )
        for name in ("shell", "exec", "powershell", "run_command"):
            with self.subTest(name=name):
                with self.assertRaises(CodexToolDenied):
                    await self.gateway.dispatch_async(name, {"command": "cmd.exe"})
        self.assertEqual(self.spec.calls, [])

    async def test_host_calls_registered_pytest(self):
        host = CodexServerHost(self.gateway)
        host.thread_id = "thread"
        host.turn_id = "turn"
        reply = await host.response_for_async({
            "id": 7, "method": "item/tool/call",
            "params": {"threadId": "thread", "turnId": "turn",
                       "namespace": None, "tool": "run_pytest",
                       "arguments": {"node_id": "tests/test_unit.py::test_one"}},
        })
        self.assertTrue(reply["result"]["success"])
        self.assertIn("one node passed", reply["result"]["contentItems"][0]["text"])

    async def test_wrong_turn_denied(self):
        host = CodexServerHost(self.gateway)
        host.thread_id = "thread"
        host.turn_id = "turn"
        reply = await host.response_for_async({
            "id": 7, "method": "item/tool/call",
            "params": {"threadId": "thread", "turnId": "other",
                       "namespace": None, "tool": "run_pytest",
                       "arguments": {"node_id": "tests/test_unit.py::test_one"}},
        })
        self.assertFalse(reply["result"]["success"])
        self.assertTrue(host.failed)
        self.assertEqual(self.spec.calls, [])


class WorktreeAndPytestBounds(unittest.TestCase):
    def test_main_checkout_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            with self.assertRaises(CodexProviderError):
                _require_linked_worktree(repo.resolve())

    def test_linked_worktree_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = root / "base"
            base.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=base, check=True)
            for key, value in (
                ("user.name", "OPSX Test"),
                ("user.email", "opsx-test@example.invalid"),
            ):
                subprocess.run(["git", "config", key, value], cwd=base, check=True)
            (base / "README").write_text("fixture", encoding="utf-8")
            subprocess.run(["git", "add", "README"], cwd=base, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=base, check=True)
            linked = root / "linked"
            subprocess.run(["git", "worktree", "add", "-b", "test-linked", str(linked)],
                           cwd=base, capture_output=True, check=True)
            _require_linked_worktree(linked.resolve())

    def test_registered_pytest_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            core = Path(directory)
            (core / "opsx" / "opsx_runtime" / "tests").mkdir(parents=True)
            spec = build_run_opsx_pytest_custom_tool(core_root=core)["run_opsx_pytest"]
            for node in (
                "tests/../../outside.py",
                "opsx/opsx_runtime/tests/../../../escape.py",
                "/etc/passwd",
                "tests/nonexistent.py",
            ):
                with self.subTest(node=node):
                    result = asyncio.run(spec.execute({"node_id": node}, None))
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["error"], "PYTEST_NODE_OUTSIDE_SCOPE")


if __name__ == "__main__":
    unittest.main()
