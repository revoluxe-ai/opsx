"""Codex provider workspace: isolated linked Git worktree for one parent run.

No automatic merge/publish. Source WIP is copied as a bounded, hashed input
snapshot; nested Git worktrees and the core submodule are never copied.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from opsx_cli.codex_turn_ledger import working_tree_revision

_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_FILES = 300
_MAX_FILE_BYTES = 4_000_000


class CodexWorkspaceError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code + (": " + detail if detail else ""))


def _git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-c", "core.longpaths=true", "-C", str(root), *args],
            capture_output=True, check=False, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CodexWorkspaceError("CODEX_WORKTREE_GIT_UNAVAILABLE") from exc
    if result.returncode:
        raise CodexWorkspaceError(
            "CODEX_WORKTREE_GIT_FAILURE",
            result.stderr.decode("utf-8", "replace")[-500:],
        )
    return result.stdout


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


_SQLITE_BUSY_SECONDS = 0.35


class _WorkspaceState:
    """Short SQLite transactions; never hold a database lock during Git I/O."""
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True,exist_ok=True)
        try:
            self.db=sqlite3.connect(path,timeout=_SQLITE_BUSY_SECONDS,isolation_level=None)
            self.db.row_factory=sqlite3.Row
            self.db.execute("PRAGMA busy_timeout=350")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute(
                """CREATE TABLE IF NOT EXISTS workspaces (
                    run_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('PREPARING','READY')),
                    source TEXT NOT NULL, source_revision TEXT NOT NULL,
                    head TEXT NOT NULL, workspace TEXT NOT NULL,
                    branch TEXT NOT NULL, overlay_json TEXT NOT NULL,
                    excluded_json TEXT NOT NULL, publish_policy TEXT NOT NULL
                )"""
            )
        except sqlite3.OperationalError as exc:
            if hasattr(self, "db"):
                self.db.close()
            raise CodexWorkspaceError("CODEX_WORKSPACE_SQLITE_BUSY") from exc

    def __enter__(self):
        return self

    def __exit__(self,*_unused):
        self.db.close()

    def get(self,run_id: str) -> dict[str,Any] | None:
        try:
            row=self.db.execute("SELECT * FROM workspaces WHERE run_id=?",(run_id,)).fetchone()
        except sqlite3.OperationalError as exc:
            raise CodexWorkspaceError("CODEX_WORKSPACE_SQLITE_BUSY") from exc
        return dict(row) if row is not None else None

    def begin(self,record: dict[str,Any]) -> None:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                """INSERT INTO workspaces VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (record["run_id"],1,"PREPARING",record["source"],
                 record["source_revision"],record["head"],record["workspace"],
                 record["branch"],json.dumps(record["overlay"],sort_keys=True),
                 json.dumps(record["excluded_nested_git"]),
                 "OWNER_GATE_NO_AUTO_MERGE"),
            )
            self.db.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self.db.execute("ROLLBACK")
            raise CodexWorkspaceError("CODEX_WORKSPACE_ALREADY_BOUND") from exc
        except sqlite3.OperationalError as exc:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise CodexWorkspaceError("CODEX_WORKSPACE_SQLITE_BUSY") from exc

    def ready(self,run_id: str) -> None:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            cursor=self.db.execute(
                "UPDATE workspaces SET status='READY' "
                "WHERE run_id=? AND status='PREPARING'",(run_id,),
            )
            if cursor.rowcount!=1:
                raise CodexWorkspaceError("CODEX_WORKSPACE_STATE_CONFLICT")
            self.db.execute("COMMIT")
        except BaseException:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise

def _overlay_snapshot(root: Path) -> tuple[list[tuple[str, bytes | None]], list[str]]:
    """Only explicit Git-visible WIP, never untracked nested Git repositories."""
    listed = _git(
        root,"ls-files","-m","-d","-o","--exclude-standard","-z",
    ).split(b"\x00")
    names = sorted({x.decode("utf-8","surrogateescape") for x in listed if x})
    if len(names)>_MAX_FILES:
        raise CodexWorkspaceError("CODEX_SOURCE_WIP_TOO_LARGE")
    overlay: list[tuple[str, bytes | None]] = []
    excluded: list[str] = []
    for name in names:
        target = (root / name).resolve()
        if not target.is_relative_to(root.resolve()) or ".." in Path(name).parts:
            raise CodexWorkspaceError("CODEX_SOURCE_WIP_PATH_ESCAPE")
        # Preserve existing OPSX core WIP and nested worktrees in source only.
        if target.is_dir() and (target / ".git").exists():
            excluded.append(name)
            continue
        if target.is_dir():
            raise CodexWorkspaceError("CODEX_WIP_DIRECTORY_UNSUPPORTED")
        if target.is_symlink():
            raise CodexWorkspaceError("CODEX_WIP_SYMLINK_UNSUPPORTED")
        if target.is_file():
            if target.stat().st_size > _MAX_FILE_BYTES:
                raise CodexWorkspaceError("CODEX_WIP_FILE_TOO_LARGE",name)
            overlay.append((name,target.read_bytes()))
        else:
            overlay.append((name,None))
    return overlay,excluded


class CodexExecutionWorkspace:
    def __init__(self, *, source: Path, run_id: str) -> None:
        self.source=Path(source).resolve()
        if not _RUN_ID.fullmatch(run_id):
            raise CodexWorkspaceError("CODEX_WORKTREE_RUN_ID_INVALID")
        self.run_id=run_id
        # Windows may reject a deep checkout even when the source itself is
        # legal. Keep worktree paths short; the complete run identity remains
        # durable in SQLite, never encoded solely by the directory name.
        label=_digest(os.path.normcase(str(self.source)).encode("utf-8"))[:20]
        leaf=_digest((str(self.source)+"\0"+run_id).encode("utf-8"))[:20]
        default_base=(Path.home()/".o") if os.name=="nt" else (
            Path(os.environ.get("LOCALAPPDATA") or Path.home()/".local"/"share")
            /"OPSX"/"codex-worktrees"
        )
        base=Path(os.environ.get("OPSX_CODEX_WORKTREE_HOME") or default_base)
        self.path=(base/label/leaf).resolve()
        # Durable manifest is outside the source and target worktree, so
        # publishing it cannot alter the bound source input revision.
        self.manifest=self.path.parent/"workspaces.sqlite3"
        self.branch="opsx/codex/"+run_id
        if self.path.is_relative_to(self.source):
            raise CodexWorkspaceError("CODEX_WORKTREE_INSIDE_SOURCE")

    async def __aenter__(self) -> Path:
        # All external commands are deterministic host operations, never
        # model-controlled shell execution.
        if not (self.source/"opsx").is_dir():
            raise CodexWorkspaceError("CODEX_SOURCE_OPSX_MISSING")
        revision=working_tree_revision(self.source)
        head=_git(self.source,"rev-parse","HEAD").decode("ascii").strip()
        with _WorkspaceState(self.manifest) as state:
            doc=state.get(self.run_id)
            if doc is not None:
                expected={
                    "schema_version":1,"status":"READY",
                    "run_id":self.run_id,"source":str(self.source),
                    "source_revision":revision,"head":head,
                    "workspace":str(self.path),"branch":self.branch,
                }
                if any(doc.get(k)!=v for k,v in expected.items()):
                    raise CodexWorkspaceError("CODEX_WORKSPACE_REPLAY_IDENTITY_MISMATCH")
                top=_git(self.path,"rev-parse","--show-toplevel").decode("utf-8","replace").strip()
                if os.path.normcase(str(Path(top).resolve()))!=os.path.normcase(str(self.path)):
                    raise CodexWorkspaceError("CODEX_WORKTREE_IDENTITY_MISMATCH")
                return self.path
        if self.path.exists():
            raise CodexWorkspaceError("CODEX_WORKTREE_EXISTING_UNBOUND")
        overlay,excluded=_overlay_snapshot(self.source)
        # A second revision check closes the source-read race before creating
        # an execution receipt. There are no source writes in this method.
        if working_tree_revision(self.source)!=revision:
            raise CodexWorkspaceError("CODEX_SOURCE_REVISION_CHANGED")
        doc={
            "schema_version":1,"status":"PREPARING",
            "run_id":self.run_id,"source":str(self.source),
            "source_revision":revision,"head":head,
            "workspace":str(self.path),"branch":self.branch,
            "overlay":{name:(None if data is None else _digest(data))
                       for name,data in overlay},
            "excluded_nested_git":excluded,
            "publish_policy":"OWNER_GATE_NO_AUTO_MERGE",
        }
        with _WorkspaceState(self.manifest) as state:
            state.begin(doc)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        try:
            _git(self.source,"worktree","add","-b",self.branch,str(self.path),head)
            for name,data in overlay:
                dest=(self.path/name).resolve()
                if not dest.is_relative_to(self.path):
                    raise CodexWorkspaceError("CODEX_OVERLAY_PATH_ESCAPE")
                if data is None:
                    if dest.exists():
                        if dest.is_dir():
                            raise CodexWorkspaceError("CODEX_OVERLAY_UNEXPECTED_DIRECTORY")
                        dest.unlink()
                else:
                    if not dest.parent.is_dir() or dest.is_symlink():
                        raise CodexWorkspaceError("CODEX_OVERLAY_PARENT_UNSUPPORTED")
                    with dest.open("wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
            if working_tree_revision(self.source)!=revision:
                raise CodexWorkspaceError("CODEX_SOURCE_REVISION_CHANGED")
            with _WorkspaceState(self.manifest) as state:
                state.ready(self.run_id)
        except BaseException:
            # PREPARING remains durable. Never automatically reset a partial
            # worktree/branch; reconcile exact workspace identity first.
            raise
        return self.path

    async def __aexit__(self,*_exc:Any)->None:
        # Preserve worktree and manifest for independent Owner review/landing.
        return None