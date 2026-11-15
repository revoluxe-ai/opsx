"""Dogfood mutation file verification."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sdk_execution_dogfood import _mutation_file_matches


class MutationFileMatchTests(unittest.TestCase):
    def test_exact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SDK_MUTATION_PROOF.txt"
            path.write_text("OPSX_SDK_MUTATING_OK", encoding="utf-8")
            self.assertTrue(_mutation_file_matches(path, "OPSX_SDK_MUTATING_OK"))

    def test_crlf_suffix_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SDK_MUTATION_PROOF.txt"
            path.write_bytes(b"OPSX_SDK_MUTATING_OK\r\n")
            self.assertTrue(_mutation_file_matches(path, "OPSX_SDK_MUTATING_OK"))

    def test_wrong_content_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SDK_MUTATION_PROOF.txt"
            path.write_text("OTHER", encoding="utf-8")
            self.assertFalse(_mutation_file_matches(path, "OPSX_SDK_MUTATING_OK"))


if __name__ == "__main__":
    unittest.main()
