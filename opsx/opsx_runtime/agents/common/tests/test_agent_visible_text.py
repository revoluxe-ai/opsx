"""Тесты санитизации agent-visible текста (без slash-команд)."""

from __future__ import annotations

import unittest

from agents.agent_visible_text import (
    sanitize_mapping_for_agent_prompt,
    strip_slash_commands_from_text,
)


class AgentVisibleTextTests(unittest.TestCase):
    def test_strip_russian_command_phrase(self) -> None:
        before = "retry (команда `/opsx-apply-red`)"
        after = strip_slash_commands_from_text(before)
        self.assertNotIn("/opsx-apply-red", after)
        self.assertNotIn("команда", after.lower())
        self.assertIn("стадия runtime", after)

    def test_sanitize_drops_primitive_command(self) -> None:
        clean = sanitize_mapping_for_agent_prompt(
            {
                "slice_id": "SLICE-001",
                "primitive_command": "/opsx-apply-red",
                "next_command": "/opsx-apply-implement change=x",
            },
        )
        self.assertEqual(clean, {"slice_id": "SLICE-001"})


if __name__ == "__main__":
    unittest.main()