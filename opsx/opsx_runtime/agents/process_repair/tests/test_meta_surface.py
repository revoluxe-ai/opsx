"""Тесты META surface и якорей opsx.mdc для ProcessFixAgent."""

from __future__ import annotations

import unittest
from pathlib import Path

from agents.process_repair.meta_surface import format_meta_surface_prompt


class MetaSurfacePromptTests(unittest.TestCase):
    def test_prompt_contains_deny_paths(self) -> None:
        text = format_meta_surface_prompt()
        self.assertIn("opsx_cli/supervisor", text)
        self.assertIn("process_repair", text)
        self.assertIn("cli_outcome.py", text)
        self.assertIn("cannot read or edit the supervisor itself", text.lower())

    def test_prompt_requires_platform_fix_not_this_change(self) -> None:
        text = format_meta_surface_prompt()
        self.assertIn("not the current change", text)
        self.assertIn("other changes", text)
        prompt = (
            Path(__file__).resolve().parents[5]
            / "opsx/contract_prompts/process_supervisor/FIX_AGENT.md"
        )
        body = prompt.read_text(encoding="utf-8")
        self.assertIn("тот же класс отказа", body)
        self.assertIn("локальной починкой", body)

    def test_opsx_mdc_anchor_sections_exist(self) -> None:
        rules = Path(__file__).resolve().parents[6] / "opsx.mdc"
        body = rules.read_text(encoding="utf-8")
        for anchor in (
            "## 20.",
            "## 21.",
            "## 26.",
            "## 27.0.2",
            "## 28.",
        ):
            self.assertIn(anchor, body, msg=anchor)


if __name__ == "__main__":
    unittest.main()