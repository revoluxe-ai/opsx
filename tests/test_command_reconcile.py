"""Semantic reconciliation blocks unsafe command replacement."""
from __future__ import annotations

import unittest

from opsx_cli.command_reconcile import reconcile_command


class CommandReconcileTests(unittest.TestCase):
    def test_proven_when_neutralized_match(self):
        body = "# /opsx-a\nshared command semantics\n"
        legacy = body
        core = body
        row = reconcile_command("opsx-a.md", legacy, core)
        self.assertEqual(row.decision, "PROVEN_EQUIVALENT")

    def test_block_on_semantic_drift(self):
        legacy = "# /opsx-a\nlegacy-only behavior\n"
        core = "# /opsx-a\ncore behavior\n"
        row = reconcile_command("opsx-a.md", legacy, core)
        self.assertEqual(row.action, "block_until_proven")


if __name__ == "__main__":
    unittest.main()