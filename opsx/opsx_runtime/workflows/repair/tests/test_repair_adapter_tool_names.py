"""Регрессия: mutating repair не шлёт устаревшие piWrite/piEdit в CreateAgent."""

from __future__ import annotations

import unittest

from agents.repair.helpers import MUTATING
from agents.adapters.repair_tool_policy import tools_for_adapter_create


class RepairAdapterToolNamesTests(unittest.TestCase):
    def test_mutating_toolset_uses_edit_not_pi_aliases(self) -> None:
        self.assertIn("edit", MUTATING)
        self.assertNotIn("piWrite", MUTATING)
        self.assertNotIn("piEdit", MUTATING)

    def test_create_agent_maps_legacy_pi_tools_to_edit(self) -> None:
        requested = ["read", "piWrite", "piEdit", "grep"]
        self.assertEqual(
            tools_for_adapter_create(requested),
            ["read", "edit", "grep"],
        )

    def test_create_agent_dedupes_after_alias(self) -> None:
        self.assertEqual(
            tools_for_adapter_create(["edit", "piWrite"]),
            ["edit"],
        )

    def test_mutating_toolset_maps_cleanly_for_adapter(self) -> None:
        self.assertEqual(
            tools_for_adapter_create(MUTATING),
            list(MUTATING),
        )


if __name__ == "__main__":
    unittest.main()
