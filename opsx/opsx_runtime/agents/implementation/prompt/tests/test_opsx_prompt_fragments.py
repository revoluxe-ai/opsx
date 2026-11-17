"""Тесты загрузки OPSX prompt-фрагментов (не .cursor/commands)."""

from __future__ import annotations

import unittest

from agents.implementation.prompt.opsx_prompt_fragments import (
    scenario_test_repair_constraints,
    verify_apply_hop_authority,
)


class OpsxPromptFragmentTests(unittest.TestCase):
    def test_verify_apply_hop_has_no_slash_commands(self) -> None:
        text = verify_apply_hop_authority()
        self.assertIn("apply-hop", text.lower())
        self.assertNotIn("/opsx-", text)
        self.assertNotIn(".cursor/commands", text)

    def test_scenario_repair_constraints_has_no_slash_commands(self) -> None:
        text = scenario_test_repair_constraints()
        self.assertNotIn("/opsx-", text)


if __name__ == "__main__":
    unittest.main()