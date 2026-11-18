"""Preserve existing split-APPLY write authority when switching providers."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opsx/opsx_runtime"))
from agents.config import ReadScope, effective_restrict_allow_for_tool
from agents.adapters.codex_app_server.codex_adapter import (
    _bind_codex_split_apply_write_scope,
)
from opsx_cli.codex_provider import CodexProviderError


class CodexSplitApplyScopeParityTests(unittest.TestCase):
    def setUp(self):
        self.scope = ReadScope(
            allow=(), deny=("**/.env", "opsx/core/**"),
            restrict_allow=("tests/**", "openspec/changes/fixture/apply-red/**"),
        )
        self.state = {
            "change_id": "fixture",
            "implementation_stage": "APPLY_RED",
            "implementation_slice_id": "s1",
        }

    def test_transport_preserves_exact_split_apply_identity(self):
        from agents.adapters.adapter_transport import adapter_transport_state
        graph_state={
            "repository_path":"C:/fixture",
            "workspace":"C:/fixture",
            **self.state,
            "model":{"id":"gpt-6-luna"},
        }
        forwarded=adapter_transport_state(graph_state)
        self.assertEqual(forwarded["implementation_stage"],"APPLY_RED")
        self.assertEqual(forwarded["implementation_slice_id"],"s1")
        self.assertEqual(forwarded["change_id"],"fixture")
        self.assertEqual(forwarded["model"],{"id":"gpt-6-luna"})

    def test_explicit_scope_equals_existing_cursor_effective_write_scope(self):
        original=effective_restrict_allow_for_tool(self.scope,"edit")
        narrowed=_bind_codex_split_apply_write_scope(
            self.scope,self.state,"apply_red",
        )
        self.assertEqual(narrowed.restrict_write_allow, original)
        self.assertEqual(narrowed.restrict_allow,self.scope.restrict_allow)
        self.assertEqual(narrowed.deny,self.scope.deny)
        self.assertEqual(narrowed.allow,self.scope.allow)
        self.assertIsNone(self.scope.restrict_write_allow)

    def test_wrong_stage_or_missing_slice_fails_closed(self):
        for state,key in (
            (dict(self.state, implementation_stage="APPLY_IMPLEMENT"),"apply_red"),
            (dict(self.state, implementation_slice_id=""),"apply_red"),
            (self.state,"test_repair"),
        ):
            with self.subTest(key=key,state=state):
                with self.assertRaises(CodexProviderError):
                    _bind_codex_split_apply_write_scope(self.scope,state,key)

    def test_empty_or_repo_wide_scope_fails_closed(self):
        for patterns in ((),("*",),("**",)):
            with self.subTest(patterns=patterns):
                scope=ReadScope(allow=(),deny=(),restrict_allow=patterns)
                with self.assertRaises(CodexProviderError):
                    _bind_codex_split_apply_write_scope(
                        scope,self.state,"apply_red",
                    )

    def test_explicit_write_scope_never_mutated(self):
        scope=ReadScope(
            allow=(),deny=("**/.env",),restrict_allow=("tests/**",),
            restrict_write_allow=("tests/scenarios/**",),
        )
        self.assertIs(
            _bind_codex_split_apply_write_scope(
                scope,{"implementation_stage":"WRONG"},"unknown",
            ),scope,
        )
