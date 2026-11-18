"""Codex delegates scope decisions to the existing OPSX policy."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agents.config import (
    ReadScope, effective_restrict_allow_for_tool, path_allowed_by_scope,
)
from opsx_cli.codex_tool_gateway import CodexToolDenied, CodexToolGateway


class CodexExistingScopeParityTests(unittest.TestCase):
    def test_scenario_authoring_inherits_existing_effective_edit_scope(self):
        scope=ReadScope(
            allow=(), deny=("opsx/**", "**/.env"),
            restrict_allow=("tests/**", "openspec/changes/**"),
        )
        expected=effective_restrict_allow_for_tool(scope,"edit")
        self.assertEqual(expected,scope.restrict_allow)
        with tempfile.TemporaryDirectory() as temporary:
            gateway=CodexToolGateway(
                Path(temporary),("edit",),
                guard=lambda *_: None, read_scope=scope,
                journal=self._journal_stub(Path(temporary)),
            )
            for path in (
                "tests/scenarios/new-tariffs/test_"+"a"*64+".py",
                "tests/unit/test_something.py",
                "openspec/changes/new-tariffs/notes.md",
            ):
                with self.subTest(path=path):
                    self.assertTrue(path_allowed_by_scope(
                        scope,path,restrict_allow=expected,
                    ))
                    gateway._check_static_write_scope(path)
            for path in ("opsx/core/secret.py", "src/other.py", ".env"):
                with self.subTest(path=path):
                    self.assertFalse(path_allowed_by_scope(
                        scope,path,restrict_allow=expected,
                    ))
                    with self.assertRaises(CodexToolDenied):
                        gateway._check_static_write_scope(path)

    def test_explicit_write_restriction_is_used_without_expansion(self):
        scope=ReadScope(
            allow=(),deny=(),restrict_allow=("tests/**",),
            restrict_write_allow=("tests/scenarios/**",),
        )
        with tempfile.TemporaryDirectory() as temporary:
            gateway=CodexToolGateway(
                Path(temporary),("edit",),
                guard=lambda *_: None,read_scope=scope,
                journal=self._journal_stub(Path(temporary)),
            )
            gateway._check_static_write_scope("tests/scenarios/test_one.py")
            with self.assertRaises(CodexToolDenied):
                gateway._check_static_write_scope("tests/unit/test_one.py")

    def test_missing_authorized_write_paths_fails_closed(self):
        scope=ReadScope(allow=(),deny=(),restrict_allow=None)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(
                CodexToolDenied,"CODEX_WRITE_SCOPE_REQUIRED",
            ):
                CodexToolGateway(
                    Path(temporary),("edit",),
                    guard=lambda *_:None,read_scope=scope,
                    journal=self._journal_stub(Path(temporary)),
                )

    @staticmethod
    def _journal_stub(root):
        class Stub:
            pass
        stub=Stub()
        stub.root=root.resolve()
        return stub
