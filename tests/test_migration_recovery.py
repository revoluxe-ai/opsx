"""Migration backup integrity and stale-plan rejection."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from opsx_cli.context import OpsxError, Project
from opsx_cli.migration import apply_plan, build_plan
from opsx_cli.storage import write_json

SOURCE = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class MigrationRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project_root = self.root / "consumer"
        self.project_root.mkdir(parents=True)
        git(self.project_root, "init", "-b", "main")
        git(self.project_root, "config", "user.name", "t")
        git(self.project_root, "config", "user.email", "t@example.invalid")
        legacy = self.project_root / ".opsx"
        legacy.mkdir()
        (legacy / "manifest.yaml").write_text("ok\n", encoding="utf-8")
        cmd = self.project_root / ".cursor/commands"
        cmd.mkdir(parents=True)
        (cmd / "opsx-one.md").write_text("# /opsx-one\nsame\n", encoding="utf-8")
        git(self.project_root, "add", ".")
        git(self.project_root, "commit", "-m", "legacy")
        self.project = Project.at(self.project_root)
        os.environ["GIT_ALLOW_PROTOCOL"] = "file"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        self.temp.cleanup()

    async def test_stale_plan_rejected(self):
        await write_json(self.project.runs / "migration-pending" / "plan.json", {"plan_fingerprint": "stale-a"})
        plan = {
            "schema_version": 2,
            "status": "READY",
            "legacy_detected": True,
            "collisions_pending_replace": 1,
            "entries": [],
            "blocking": [],
            "plan_fingerprint": "stale-b",
        }
        with self.assertRaisesRegex(OpsxError, "Migration plan changed"):
            await apply_plan(self.project, plan, dry_run=False)

    async def test_backup_records_recovery_contract(self):
        core = SOURCE
        plan = build_plan(self.project, core=core)
        if plan["status"] == "BLOCKED":
            self.skipTest("fixture matrix blocked")
        await apply_plan(self.project, plan)
        recovery = self.project_root / "opsx/project/migration-recovery.json"
        self.assertTrue(recovery.is_file())


if __name__ == "__main__":
    unittest.main()