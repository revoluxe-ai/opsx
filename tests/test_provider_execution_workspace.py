"""Provider workspace swap: original OPSX graph, isolated Codex Git worktree."""
from __future__ import annotations
import sqlite3
from contextlib import closing
import time
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"opsx"/"opsx_runtime"))

from agents.adapters.execution_workspace_port import open_execution_workspace
from agents.adapters.codex_app_server.execution_workspace import CodexWorkspaceError


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git","-C",str(root),*args],check=True,
        capture_output=True,text=True,
    ).stdout.strip()


class ProviderExecutionWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp=tempfile.TemporaryDirectory(prefix="opsx-workspace-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/"project";self.source.mkdir()
        git(self.source,"init","-q")
        git(self.source,"config","user.name","OPSX")
        git(self.source,"config","user.email","opsx@invalid.example")
        (self.source/"opsx").mkdir()
        (self.source/"opsx/.keep").write_text("")
        (self.source/"demo.txt").write_text("tracked original")
        git(self.source,"add",".")
        git(self.source,"commit","-qm","fixture")
        self.env=patch.dict(os.environ,{
            "LOCALAPPDATA":str(self.root/"local"),
            "OPSX_CODEX_WORKTREE_HOME":str(self.root/"local"/"codex-worktrees"),
        })
        self.env.start();self.addCleanup(self.env.stop)

    async def test_cursor_uses_original_checkout(self):
        async with open_execution_workspace(
            self.source,run_id="fixture",provider_module="agents.adapters.cursor_sdk",
        ) as workspace:
            self.assertEqual(workspace,self.source.resolve())
        self.assertFalse((self.root/"local").exists())

    async def test_codex_isolated_copy_preserves_bounded_source_wip(self):
        (self.source/"demo.txt").write_text("tracked WIP")
        (self.source/"new.txt").write_text("untracked WIP")
        async with open_execution_workspace(
            self.source,run_id="fixture-one",
            provider_module="agents.adapters.codex_app_server",
        ) as workspace:
            self.assertNotEqual(workspace,self.source)
            self.assertEqual((workspace/"demo.txt").read_text(),"tracked WIP")
            self.assertEqual((workspace/"new.txt").read_text(),"untracked WIP")
            self.assertEqual((self.source/"demo.txt").read_text(),"tracked WIP")
            (workspace/"demo.txt").write_text("model changed")
            self.assertEqual((self.source/"demo.txt").read_text(),"tracked WIP")
            self.assertEqual(
                git(workspace,"rev-parse","--abbrev-ref","HEAD"),
                "opsx/codex/fixture-one",
            )
        # The durable worktree must remain for Owner gate; it is not merged.
        self.assertTrue(workspace.is_dir())
        async with open_execution_workspace(
            self.source,run_id="fixture-one",
            provider_module="agents.adapters.codex_app_server",
        ) as resumed:
            self.assertEqual(resumed,workspace)
            self.assertEqual((resumed/"demo.txt").read_text(),"model changed")
        manifest=workspace.parent/"workspaces.sqlite3"
        with closing(sqlite3.connect(manifest)) as db:
            record=db.execute(
                "SELECT status,publish_policy,overlay_json FROM workspaces WHERE run_id=?",
                ("fixture-one",),
            ).fetchone()
        self.assertEqual(record[0],"READY")
        self.assertEqual(record[1],"OWNER_GATE_NO_AUTO_MERGE")
        self.assertNotIn("tracked WIP",str(record))
        self.assertFalse(list(workspace.parent.glob("*.manifest.json")))

    async def test_changed_source_cannot_reuse_old_execution(self):
        async with open_execution_workspace(
            self.source,run_id="one",
            provider_module="agents.adapters.codex_app_server",
        ):pass
        (self.source/"demo.txt").write_text("new source revision")
        with self.assertRaisesRegex(
            CodexWorkspaceError, "CODEX_WORKSPACE_REPLAY_IDENTITY_MISMATCH",
        ):
            async with open_execution_workspace(
                self.source,run_id="one",
                provider_module="agents.adapters.codex_app_server",
            ):pass

    async def test_partially_created_manifest_fails_closed(self):
        runner=open_execution_workspace(
            self.source,run_id="partial",
            provider_module="agents.adapters.codex_app_server",
        )
        from agents.adapters.codex_app_server.execution_workspace import _WorkspaceState
        with _WorkspaceState(runner.manifest) as state:
            state.begin({
                "run_id":"partial", "source":str(self.source),
                "source_revision":"0"*64, "head":"0"*40,
                "workspace":str(runner.path),"branch":runner.branch,
                "overlay":{},"excluded_nested_git":[],
            })
        with self.assertRaisesRegex(
            CodexWorkspaceError, "CODEX_WORKSPACE_REPLAY_IDENTITY_MISMATCH",
        ):
            async with runner:pass
        self.assertFalse(runner.path.exists())

    async def test_invalid_run_id_denied(self):
        with self.assertRaisesRegex(
            CodexWorkspaceError, "CODEX_WORKTREE_RUN_ID_INVALID",
        ):
            open_execution_workspace(
                self.source,run_id="../../escape",
                provider_module="agents.adapters.codex_app_server",
            )

    async def test_sqlite_writer_busy_fails_bounded_not_hangs(self):
        from agents.adapters.codex_app_server.execution_workspace import _WorkspaceState
        runner=open_execution_workspace(
            self.source,run_id="locked",
            provider_module="agents.adapters.codex_app_server",
        )
        with _WorkspaceState(runner.manifest) as first:
            first.db.execute("BEGIN IMMEDIATE")
            started=time.monotonic()
            try:
                with _WorkspaceState(runner.manifest) as second:
                    with self.assertRaisesRegex(
                        CodexWorkspaceError,"CODEX_WORKSPACE_SQLITE_BUSY",
                    ):
                        second.begin({
                            "run_id":"locked","source":str(self.source),
                            "source_revision":"a"*64,"head":"b"*40,
                            "workspace":str(runner.path),"branch":runner.branch,
                            "overlay":{},"excluded_nested_git":[],
                        })
            finally:
                first.db.execute("ROLLBACK")
            self.assertLess(time.monotonic()-started,2.0)

    async def test_multiple_runs_share_one_sqlite_manifest(self):
        for run_id in ("alpha","beta"):
            async with open_execution_workspace(
                self.source,run_id=run_id,
                provider_module="agents.adapters.codex_app_server",
            ):
                pass
        db_path=self.root/"local"/"codex-worktrees"
        manifests=list(db_path.rglob("workspaces.sqlite3"))
        self.assertEqual(len(manifests),1)
        with closing(sqlite3.connect(manifests[0])) as db:
            names=[row[0] for row in db.execute(
                "SELECT run_id FROM workspaces ORDER BY run_id"
            )]
        self.assertEqual(names,["alpha","beta"])

    async def test_foreign_preexisting_path_refuses_overwrite(self):
        runner=open_execution_workspace(
            self.source,run_id="existing",
            provider_module="agents.adapters.codex_app_server",
        )
        runner.path.mkdir(parents=True)
        (runner.path/"data").write_text("foreign file")
        with self.assertRaisesRegex(
            CodexWorkspaceError, "CODEX_WORKTREE_EXISTING_UNBOUND",
        ):
            async with runner:pass
        self.assertEqual((runner.path/"data").read_text(),"foreign file")