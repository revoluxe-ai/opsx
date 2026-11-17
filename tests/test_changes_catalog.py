"""Unit-тесты каталога OpenSpec change для панели."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from opsx_cli.changes_catalog import list_openspec_changes


class ChangesCatalogTests(unittest.TestCase):
    def test_lists_changes_with_preview_and_skips_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            changes = root / "openspec" / "changes"
            (changes / "archive").mkdir(parents=True)
            (changes / "my-feature").mkdir()
            (changes / "my-feature" / "proposal.md").write_text(
                "# Новые тарифы\n\nОписание.\n",
                encoding="utf-8",
            )
            (changes / "skip bad name").mkdir()

            items = list_openspec_changes(root)

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["id"], "my-feature")
            self.assertEqual(items[0]["preview"], "Новые тарифы")

    def test_newest_change_is_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            changes = root / "openspec" / "changes"
            older = changes / "older-change"
            newer = changes / "newer-change"
            older.mkdir(parents=True)
            newer.mkdir(parents=True)
            (older / "proposal.md").write_text("# Старый\n", encoding="utf-8")
            (newer / "proposal.md").write_text("# Свежий\n", encoding="utf-8")
            past = time.time() - 86_400
            os.utime(older / "proposal.md", (past, past))
            os.utime(newer / "proposal.md", None)

            items = list_openspec_changes(root)

            self.assertEqual([item["id"] for item in items], ["newer-change", "older-change"])


if __name__ == "__main__":
    unittest.main()