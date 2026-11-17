"""Managed commands must not ship donor execution authority."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from opsx_cli.authority import assert_portable_managed_content, neutralize_command_authority
from opsx_cli.installer import _managed_payloads

SOURCE = Path(__file__).resolve().parents[1]


class AuthorityTests(unittest.TestCase):
    def test_neutralize_removes_stroy_heading(self):
        sample = "# STROY-SYSTEM TARGET OVERRIDE - HIGHEST PRIORITY\nFor stroy-system\n\n# /opsx-x\nbody\n"
        out = neutralize_command_authority(sample)
        assert_portable_managed_content("fixture.md", out)
        self.assertNotIn("STROY-SYSTEM", out)

    def test_managed_payloads_are_portable(self):
        with patch("opsx_cli.installer._opsx_context_source", return_value=SOURCE / "tests/fixtures/opsx.mdc"):
            payloads = _managed_payloads(SOURCE)
        for relative, body in payloads.items():
            text = body.decode("utf-8")
            assert_portable_managed_content(relative, text)
            self.assertIn("PROJECT TARGET PROFILE", text)


if __name__ == "__main__":
    unittest.main()