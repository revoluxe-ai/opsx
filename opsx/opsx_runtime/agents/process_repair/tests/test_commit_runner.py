"""Unit tests for deterministic supervisor commit_journal_entry."""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from agents.process_repair.commit_runner import commit_journal_entry
from opsx_cli.supervisor_journal import SupervisorJournal


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class CommitJournalEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.core = self.root / "core"
        self.core.mkdir()
        _git(self.root, "init")
        _git(self.core, "init")
        (self.core / "opsx").mkdir(parents=True)
        self.target = self.core / "opsx" / "note.txt"
        self.target.write_text("hello", encoding="utf-8")
        _git(self.core, "checkout", "-b", "supervisor-fix")
        _git(self.core, "add", ".")
        _git(self.core, "commit", "-m", "seed")
        self.sha = hashlib.sha256(self.target.read_bytes()).hexdigest()
        self.journal = self.root / "journal.sqlite"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_entry(self, **overrides: object) -> str:
        entry_id = "abc123"
        row = {
            "entry_id": entry_id,
            "files_changed": [
                {"repo": "core", "path": "opsx/note.txt", "sha256": self.sha},
            ],
        }
        row.update(overrides)
        SupervisorJournal(self.journal).append_row(row)
        return entry_id

    def test_commits_when_sha_matches(self) -> None:
        self.target.write_text("patched", encoding="utf-8")
        new_sha = hashlib.sha256(self.target.read_bytes()).hexdigest()
        entry_id = self._write_entry(
            files_changed=[{"repo": "core", "path": "opsx/note.txt", "sha256": new_sha}],
        )
        outcome = commit_journal_entry(
            project_root=self.root,
            core_root=self.core,
            journal_path=self.journal,
            entry_id=entry_id,
        )
        self.assertEqual(outcome.status, "COMMITTED")
        self.assertTrue(outcome.commit_sha)

    def test_blocks_sha_mismatch(self) -> None:
        entry_id = self._write_entry(files_changed=[{"repo": "core", "path": "opsx/note.txt", "sha256": "0" * 64}])
        outcome = commit_journal_entry(
            project_root=self.root,
            core_root=self.core,
            journal_path=self.journal,
            entry_id=entry_id,
        )
        self.assertEqual(outcome.status, "BLOCKED")
        self.assertIn("sha256 mismatch", outcome.error or "")

    def test_scopes_commit_to_fix_report_paths_only(self) -> None:
        noise = self.core / "opsx" / "noise.txt"
        noise.write_text("n", encoding="utf-8")
        self.target.write_text("patched", encoding="utf-8")
        new_sha = hashlib.sha256(self.target.read_bytes()).hexdigest()
        noise_sha = hashlib.sha256(noise.read_bytes()).hexdigest()
        entry_id = "scope1"
        SupervisorJournal(self.journal).append_row(
            {
                "entry_id": entry_id,
                "fix_report": {"files_changed": ["opsx/note.txt"]},
                "files_changed": [
                    {"repo": "core", "path": "opsx/note.txt", "sha256": new_sha},
                    {"repo": "core", "path": "opsx/noise.txt", "sha256": noise_sha},
                ],
            }
        )
        outcome = commit_journal_entry(
            project_root=self.root,
            core_root=self.core,
            journal_path=self.journal,
            entry_id=entry_id,
        )
        self.assertEqual(outcome.status, "COMMITTED")
        log = _git(self.core, "show", "--name-only", "--pretty=format:", "HEAD")
        names = {line.strip() for line in log.stdout.splitlines() if line.strip()}
        self.assertEqual(names, {"opsx/note.txt"})

    def test_blocks_protected_branch(self) -> None:
        _git(self.core, "checkout", "-b", "main")
        self.target.write_text("patched", encoding="utf-8")
        new_sha = hashlib.sha256(self.target.read_bytes()).hexdigest()
        entry_id = self._write_entry(
            files_changed=[{"repo": "core", "path": "opsx/note.txt", "sha256": new_sha}],
        )
        outcome = commit_journal_entry(
            project_root=self.root,
            core_root=self.core,
            journal_path=self.journal,
            entry_id=entry_id,
        )
        self.assertEqual(outcome.status, "BLOCKED")
        self.assertIn("protected branch", outcome.error or "")


if __name__ == "__main__":
    unittest.main()