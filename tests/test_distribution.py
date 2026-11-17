"""Real Git consumer tests. No provider calls, product edits or database required."""
from __future__ import annotations

import asyncio
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from opsx_cli.context import OpsxError, Project
from opsx_cli.installer import install, upgrade, uninstall, doctor
from opsx_cli.service import start_panel, stop_panel, panel_status
from opsx.project_context import bind_project_context, reset_project_context, resource_path
from opsx_cli.lease import execution_lease

SOURCE = Path(__file__).resolve().parents[1]


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def init(root: Path) -> None:
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "OPSX test")
    git(root, "config", "user.email", "opsx-test@example.invalid")
    (root / "README").write_text("fixture\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture")


class DistributionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "fastapi>=0.115", "uvicorn>=0.30"],
            check=False,
        )
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin"
        init(self.origin)
        for rel in ["opsx_cli", "opsx/opsx_runtime/run_verify_impl.py",
                    "opsx_tools/registry/apply_design.schema.json"]:
            src, dest = SOURCE / rel, self.origin / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copyfile(src, dest)
        (self.origin / "pyproject.toml").write_text((SOURCE / "pyproject.toml").read_text())
        commands = self.origin / ".cursor/commands"
        commands.mkdir(parents=True)
        (commands / "opsx-one.md").write_text("read opsx_tools/one.mjs; settings opsx/project/project.json\n")
        git(self.origin, "add", ".")
        git(self.origin, "commit", "-m", "v1")
        git(self.origin, "tag", "v1")
        self.projects = [Project.at(self.root / name) for name in ("alpha", "beta")]
        for project in self.projects:
            init(project.root)
        self.old_protocol = os.environ.get("GIT_ALLOW_PROTOCOL")
        os.environ["GIT_ALLOW_PROTOCOL"] = "file"

    def tearDown(self):
        if self.old_protocol is None:
            os.environ.pop("GIT_ALLOW_PROTOCOL", None)
        else:
            os.environ["GIT_ALLOW_PROTOCOL"] = self.old_protocol
        # Git files may be read-only on Windows.
        def writable(function, path, exc):
            os.chmod(path, 0o700)
            function(path)
        shutil.rmtree(self.root, onerror=writable)
        self.temp.cleanup()

    def _seed_opsx_context(self, project: Project) -> None:
        dest_dir = project.root / "opsx"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE.parent / "opsx.mdc", dest_dir / "opsx.mdc")

    async def installed(self, project=None):
        project = project or self.projects[0]
        self._seed_opsx_context(project)
        return await install(project, repository=str(self.origin), ref="v1", no_runtime=True)

    def version_two(self, broken=False):
        p = self.origin / ".cursor/commands/opsx-one.md"
        p.write_text("v2 content\n")
        (p.parent / "opsx-two.md").write_text("new command\n")
        if broken:
            (self.origin / "opsx_cli/cli.py").unlink()
        git(self.origin, "add", ".")
        git(self.origin, "commit", "-m", "v2")
        git(self.origin, "tag", "v2")

    async def test_fresh_install_has_submodule_commands_and_manual_context(self):
        result = await self.installed()
        project = self.projects[0]
        self.assertEqual(result["status"], "INSTALLED")
        self.assertTrue((project.core / ".git").is_file())
        self.assertIn("opsx/core/opsx_tools/", (project.root / ".cursor/commands/opsx-one.md").read_text(encoding="utf-8"))
        self.assertIn("alwaysApply: false", (project.root / "opsx/opsx.mdc").read_text(encoding="utf-8"))
        self.assertFalse((project.root / "AGENTS.md").exists())
        self.assertFalse((project.root / "src").exists())
        self.assertFalse((project.core / "opsx/project").exists())

    async def test_install_is_idempotent_and_preserves_settings(self):
        await self.installed()
        project = self.projects[0]
        project.settings.write_text('{"custom": true}')
        result = await self.installed()
        self.assertEqual(result["status"], "ALREADY_INSTALLED")
        self.assertEqual(json.loads(project.settings.read_text(encoding="utf-8")), {"custom": True})

    async def test_two_projects_are_independent(self):
        await self.installed(self.projects[0])
        await self.installed(self.projects[1])
        first = json.loads(self.projects[0].settings.read_text(encoding="utf-8"))
        second = json.loads(self.projects[1].settings.read_text(encoding="utf-8"))
        self.assertNotEqual(first["project_id"], second["project_id"])
        self.assertNotEqual(self.projects[0].core, self.projects[1].core)

    async def test_existing_install_initializes_submodule_after_project_clone(self):
        await self.installed()
        original = self.projects[0]
        git(original.root, "add", ".")
        git(original.root, "commit", "-m", "installed")
        clone = self.root / "consumer-clone"
        git(self.root, "clone", str(original.root), str(clone))
        result = await install(Project.at(clone), no_runtime=True)
        self.assertEqual(result["status"], "ALREADY_INSTALLED")
        self.assertTrue((clone / "opsx/core/opsx_cli/cli.py").is_file())

    async def test_upgrade_preserves_config_receipts_and_other_cursor_files(self):
        await self.installed()
        project = self.projects[0]
        project.settings.write_text('{"custom": "preserved"}')
        receipt = project.root / "opsx/receipts/change/hop.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text('{"accepted":true}')
        unrelated = project.root / ".cursor/commands/my-project.md"
        unrelated.write_text("local")
        self.version_two()
        result = await upgrade(project, ref="v2", no_runtime=True)
        self.assertEqual(result["status"], "UPGRADED")
        self.assertEqual(project.settings.read_text(encoding="utf-8"), '{"custom": "preserved"}')
        self.assertEqual(receipt.read_text(encoding="utf-8"), '{"accepted":true}')
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "local")
        self.assertTrue((project.root / ".cursor/commands/opsx-two.md").exists())

    async def test_manual_command_conflict_prevents_upgrade(self):
        before = await self.installed()
        project = self.projects[0]
        (project.root / ".cursor/commands/opsx-one.md").write_text("user edit")
        self.version_two()
        with self.assertRaisesRegex(OpsxError, "Local edits"):
            await upgrade(project, ref="v2", no_runtime=True)
        self.assertEqual(git(project.core, "rev-parse", "HEAD"), before["core_sha"])

    async def test_upgrade_refuses_active_worker_without_web_panel(self):
        before = await self.installed()
        self.version_two()
        async with execution_lease(self.projects[0]):
            with self.assertRaisesRegex(OpsxError, "execution"):
                await upgrade(self.projects[0], ref="v2", no_runtime=True)
        self.assertEqual(git(self.projects[0].core, "rev-parse", "HEAD"), before["core_sha"])

    async def test_process_exit_releases_execution_lease(self):
        project = self.projects[0]
        script = "from pathlib import Path; from opsx_cli.lease import ExecutionLease; import time; lock=ExecutionLease(Path(" + repr(str(project.runs / "execution.lock")) + ")).acquire(); print('READY',flush=True); time.sleep(60)"
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", script, cwd=SOURCE,
                                                     stdout=asyncio.subprocess.PIPE)
        try:
            self.assertEqual((await asyncio.wait_for(child.stdout.readline(), 5)).strip(), b"READY")
            with self.assertRaises(OpsxError):
                async with execution_lease(project):
                    self.fail("Concurrent execution lease acquired")
        finally:
            child.terminate()
            await child.wait()
        async with execution_lease(project):
            self.assertTrue((project.runs / "execution.lock").exists())

    async def test_invalid_core_rolls_back_old_pin_and_assets(self):
        before = await self.installed()
        self.version_two(broken=True)
        with self.assertRaises(OpsxError):
            await upgrade(self.projects[0], ref="v2", no_runtime=True)
        self.assertEqual(git(self.projects[0].core, "rev-parse", "HEAD"), before["core_sha"])
        self.assertEqual(json.loads(self.projects[0].installation.read_text())["core_sha"], before["core_sha"])

    async def test_failed_environment_preserves_old_version(self):
        before = await self.installed()
        self.version_two()
        with patch("opsx_cli.installer._prepare_runtime", side_effect=OpsxError("ENV_FAIL", "fixture failure")):
            with self.assertRaises(OpsxError):
                await upgrade(self.projects[0], ref="v2")
        self.assertEqual(git(self.projects[0].core, "rev-parse", "HEAD"), before["core_sha"])

    async def test_install_retry_resumes_owned_incomplete_checkout(self):
        project = self.projects[0]
        with patch("opsx_cli.installer._prepare_runtime", side_effect=OpsxError("ENV_FAIL", "fixture")):
            with self.assertRaises(OpsxError):
                await install(project, repository=str(self.origin), ref="v1")
        result = await install(project, repository=str(self.origin), ref="v1", no_runtime=True)
        self.assertEqual(result["status"], "INSTALLED")
        self.assertFalse((project.runs / "installation-pending.json").exists())

    async def test_doctor_does_not_claim_runtime_ready_when_omitted(self):
        await self.installed()
        result = await doctor(self.projects[0])
        self.assertEqual(result["status"], "NOT_READY")
        self.assertFalse(next(c["ok"] for c in result["checks"] if c["name"] == "runtime_environment"))

    @unittest.skipIf(os.name == "nt", "Creating symbolic links may need a Windows developer privilege")
    async def test_install_refuses_cursor_symlink_outside_project(self):
        project = self.projects[0]
        foreign = self.root / "foreign-cursor"
        foreign.mkdir()
        (project.root / ".cursor").symlink_to(foreign, target_is_directory=True)
        with self.assertRaisesRegex(OpsxError, "escapes project"):
            await self.installed()
        self.assertEqual(list(foreign.iterdir()), [])

    async def test_uninstall_preserves_user_project_data(self):
        await self.installed()
        project = self.projects[0]
        git(project.root, "add", ".")
        git(project.root, "commit", "-m", "adopt")
        settings = project.settings.read_bytes()
        result = await uninstall(project)
        self.assertEqual(result["status"], "UNINSTALLED")
        self.assertEqual(project.settings.read_bytes(), settings)
        self.assertFalse((project.root / "opsx/opsx.mdc").exists())

    async def test_panel_start_is_idempotent_and_stop_confirms_ownership(self):
        await self.installed()
        project = self.projects[0]
        # Exercise the real web server without installing model dependencies.
        meta = json.loads(project.installation.read_text())
        meta["runtime_python"] = sys.executable
        project.installation.write_text(json.dumps(meta))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        try:
            first = await start_panel(project, port=port, browser=False)
            second = await start_panel(project, port=port, browser=False)
            self.assertEqual(first["status"], "STARTED")
            self.assertEqual(second["status"], "ALREADY_RUNNING")
            self.assertEqual(first["instance_id"], second["instance_id"])
            state = json.loads(project.panel_state.read_text())
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
            )
            home = opener.open(state["url"] + "/").read().decode("utf-8")
            self.assertIn("Запуск workflow", home)
            self.assertIn("Implementation", home)
            self.assertNotIn('href="/repair"', home)
            self.assertNotIn("Ремонт", home)
            self.assertIn("workflow-timeout-min", home)
            self.assertIn("supervisor-timeout-min", home)
            with self.assertRaises(urllib.error.HTTPError) as missing:
                opener.open(state["url"] + "/repair")
            self.assertEqual(missing.exception.code, 404)
            self.assertEqual((await stop_panel(project))["status"], "STOPPED")
            self.assertFalse((await panel_status(project))["running"])
        finally:
            await stop_panel(project)


class PanelWorkflowRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_post_returns_run_id(self):
        """POST /api/workflows отдаёт run_id и передаёт его в RunService.execute."""
        import httpx
        from httpx import ASGITransport

        from opsx_cli.panel import create_app

        project_root = tempfile.mkdtemp()
        project = Project.at(project_root)
        await asyncio.to_thread(project.runs.mkdir, parents=True, exist_ok=True)
        # Provider selection is part of the installed project contract.
        project.settings.parent.mkdir(parents=True, exist_ok=True)
        project.settings.write_text(json.dumps({
            "schema_version": 2,
            "instance": {
                "provider": "cursor",
                "adapter": {"module": "agents.adapters.cursor_sdk", "extras": ["cursor"]},
            },
        }), encoding="utf-8")
        state = {
            "instance_id": "test",
            "token": "panel-test-token",
            "url": "http://127.0.0.1:8765",
            "project_root": str(project.root),
        }
        captured: dict = {}

        async def fake_execute(self, **kwargs):
            captured.update(kwargs)
            return {"status": "PASS", "run_id": kwargs.get("run_id")}

        app = create_app(project, state)
        transport = ASGITransport(app=app)
        with patch("opsx_cli.panel.RunService.execute", new=fake_execute):
            async with httpx.AsyncClient(
                transport=transport,
                base_url=state["url"],
                headers={"host": "127.0.0.1:8765"},
                cookies={"opsx_token": state["token"]},
            ) as client:
                response = await client.post(
                    "/api/workflows",
                    json={
                        "change_id": "visual",
                        "workflow_timeout_minutes": 45,
                        "supervisor_timeout_minutes": 180,
                    },
                )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["change_id"], "visual")
            self.assertTrue(body.get("run_id"))
            self.assertEqual(len(body["run_id"]), 32)
            self.assertEqual(captured.get("mode"), "workflow")
            self.assertEqual(captured.get("provider"), "cursor")
            self.assertEqual(captured.get("change"), "visual")
            self.assertEqual(captured.get("run_id"), body["run_id"])


class PathTests(unittest.TestCase):
    def test_core_resources_and_project_data_resolve_separately(self):
        project = Path(tempfile.gettempdir()) / "opsx-path-project"
        core = project / "opsx/core"
        token = bind_project_context(project, core)
        try:
            self.assertEqual(resource_path(project, "opsx_tools", "one.mjs"), (core / "opsx_tools/one.mjs").resolve())
            self.assertEqual(resource_path(project, "opsx", "project", "capabilities.yaml"),
                             (project / "opsx/project/capabilities.yaml").resolve())
            self.assertEqual(resource_path(project, "opsx", "receipts", "a.json"),
                             (project / "opsx/receipts/a.json").resolve())
            self.assertEqual(resource_path(project, "openspec", "changes", "a"),
                             (project / "openspec/changes/a").resolve())
        finally:
            reset_project_context(token)


if __name__ == "__main__":
    unittest.main()
