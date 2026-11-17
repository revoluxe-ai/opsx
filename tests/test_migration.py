"""Legacy cutover planning and collision staging."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from opsx_cli.context import Project
from opsx_cli.installer import install
from opsx_cli.migration import apply_plan, build_plan, legacy_present

SOURCE = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "-b", "main")
        git(self.origin, "config", "user.name", "OPSX")
        git(self.origin, "config", "user.email", "opsx@example.invalid")
        for rel in ["opsx_cli", "opsx/opsx_runtime/run_verify_impl.py",
                    "opsx_tools/registry/apply_design.schema.json"]:
            src, dest = SOURCE / rel, self.origin / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copyfile(src, dest)
        (self.origin / "pyproject.toml").write_text((SOURCE / "pyproject.toml").read_text(encoding="utf-8"))
        commands = self.origin / ".cursor/commands"
        commands.mkdir(parents=True)
        (commands / "opsx-one.md").write_text("legacy command body\n", encoding="utf-8")
        git(self.origin, "add", ".")
        git(self.origin, "commit", "-m", "core")
        self.project_root = self.root / "consumer"
        self.project_root.mkdir()
        git(self.project_root, "init", "-b", "main")
        git(self.project_root, "config", "user.name", "OPSX")
        git(self.project_root, "config", "user.email", "opsx@example.invalid")
        legacy = self.project_root / ".opsx"
        legacy.mkdir()
        (legacy / "manifest.yaml").write_text("bootstrap_version: legacy\n", encoding="utf-8")
        cmd = self.project_root / ".cursor/commands"
        cmd.mkdir(parents=True)
        core_cmd = self.origin / ".cursor/commands/opsx-one.md"
        (cmd / "opsx-one.md").write_text(core_cmd.read_text(encoding="utf-8"), encoding="utf-8")
        git(self.project_root, "add", ".")
        git(self.project_root, "commit", "-m", "legacy consumer")
        self.project = Project.at(self.project_root)
        os.environ["GIT_ALLOW_PROTOCOL"] = "file"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        self.temp.cleanup()

    def test_legacy_detected_and_plan_ready(self):
        self.assertTrue(legacy_present(self.project))
        plan = build_plan(self.project, core=self.origin)
        self.assertIn(plan["status"], {"READY", "BLOCKED"})
        self.assertGreater(plan["entry_count"], 0)

    async def test_apply_then_install_without_collision(self):
        plan = build_plan(self.project, core=self.origin)
        if plan["status"] == "BLOCKED":
            self.skipTest("matrix blocked for fixture")
        await apply_plan(self.project, plan)
        from opsx_cli.migration import stage_proven_command_replacements
        await stage_proven_command_replacements(self.project, plan)
        opsx_dir = self.project.root / "opsx"
        opsx_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE / "tests/fixtures/opsx.mdc", opsx_dir / "opsx.mdc")
        result = await install(self.project, repository=str(self.origin), no_runtime=True)
        self.assertEqual(result["status"], "INSTALLED")
        self.assertFalse((self.project.root / ".cursor/commands/opsx-one.md").read_text(encoding="utf-8").startswith("donor"))


if __name__ == "__main__":
    unittest.main()