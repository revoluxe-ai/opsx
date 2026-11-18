"""Crash-recoverable before-image journal for Codex host-side file edits.

Journal lives outside the source tree. A lock serializes write turns for one
worktree; SQLite commits before every host write. On interruption a later turn
can restore the prior bytes, or PAUSE if revision identity became ambiguous.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Any


class WriteRecoveryError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_journal_path(root: Path) -> Path:
    location = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    identity = hashlib.sha256(os.path.normcase(str(root.resolve())).encode("utf-8")).hexdigest()
    return location / "OPSX" / "codex-write-recovery" / f"{identity}.sqlite3"


class CodexWriteJournal:
    def __init__(self, root: Path, *, path: Path | None = None):
        self.root = root.resolve()
        self.path = (path or default_journal_path(self.root)).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_relative_to(self.root):
            raise WriteRecoveryError("CODEX_JOURNAL_INSIDE_WORKTREE")
        self._lock_file = None
        self.db = sqlite3.connect(self.path, timeout=0.35, isolation_level=None)
        self.db.execute("PRAGMA busy_timeout=350")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS pending_edits(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_root TEXT NOT NULL,
                rel_path TEXT NOT NULL,
                before_data BLOB NOT NULL,
                before_digest TEXT NOT NULL,
                after_digest TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('PENDING','ROLLED_BACK','CONFIRMED')),
                occurred_at TEXT NOT NULL
            )
        """)
        # Existing edit journals remain valid; CREATE is a distinct
        # reversible operation with an absent-file precondition.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(pending_edits)")}
        if "op_kind" not in columns:
            self.db.execute(
                "ALTER TABLE pending_edits ADD COLUMN op_kind TEXT NOT NULL "
                "DEFAULT 'REPLACE' CHECK(op_kind IN ('REPLACE','CREATE'))"
            )

    def __enter__(self):
        lock_path = self.path.with_suffix(".lock")
        stream = open(lock_path, "a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            self.db.close()
            raise WriteRecoveryError("CODEX_WORKTREE_WRITE_LOCKED") from exc
        self._lock_file = stream
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._lock_file:
                self._lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()
                self._lock_file = None
        finally:
            self.db.close()

    def _require_lock(self):
        if self._lock_file is None:
            raise WriteRecoveryError("CODEX_JOURNAL_LOCK_REQUIRED")

    def _target(self, relative: str) -> Path:
        rel = Path(relative)
        if rel.is_absolute() or ".." in rel.parts or ":" in relative or not relative:
            raise WriteRecoveryError("CODEX_RECOVERY_PATH_INVALID")
        target = (self.root / rel).resolve()
        if not target.is_relative_to(self.root):
            raise WriteRecoveryError("CODEX_RECOVERY_PATH_ESCAPE")
        return target

    def begin(self, rel_path: str, before: bytes, after: bytes) -> int:
        """DURABLE PENDING before the corresponding os.replace."""
        self._require_lock()
        target = self._target(rel_path)
        if not target.is_file() or target.read_bytes() != before:
            raise WriteRecoveryError("CODEX_JOURNAL_REVISION_CHANGED")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.db.execute(
                """INSERT INTO pending_edits
                (repo_root,rel_path,before_data,before_digest,after_digest,state,occurred_at)
                VALUES(?,?,?,?,?,'PENDING',?)""",
                (str(self.root), rel_path, before, _digest(before), _digest(after),
                 datetime.now(timezone.utc).isoformat()),
            )
            receipt = cursor.lastrowid
            self.db.execute("COMMIT")
            return int(receipt)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def begin_create(self, rel_path: str, after: bytes) -> int:
        """Durable absent-file intent; caller must use atomic no-clobber create."""
        self._require_lock()
        target = self._target(rel_path)
        if target.exists() or not target.parent.is_dir():
            raise WriteRecoveryError("CODEX_CREATE_PRECONDITION_FAILED")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            cursor = self.db.execute(
                """INSERT INTO pending_edits
                (repo_root,rel_path,before_data,before_digest,after_digest,state,
                 occurred_at,op_kind)
                VALUES(?,?,?,?,?,'PENDING',?,'CREATE')""",
                (str(self.root), rel_path, b"", _digest(b""), _digest(after),
                 datetime.now(timezone.utc).isoformat()),
            )
            receipt = cursor.lastrowid
            self.db.execute("COMMIT")
            return int(receipt)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def _pending(self):
        return self.db.execute(
            """SELECT id,repo_root,rel_path,before_data,before_digest,after_digest,op_kind
               FROM pending_edits WHERE state='PENDING' ORDER BY id DESC"""
        ).fetchall()

    def recover(self, atomic_replace: Callable[[Path, bytes], None]) -> int:
        """Reverse pending edits in order; do not overwrite foreign modifications."""
        self._require_lock()
        count = 0
        for receipt, root, rel, before, prior_sha, after_sha, op_kind in self._pending():
            if root != str(self.root) or _digest(bytes(before)) != prior_sha:
                raise WriteRecoveryError("CODEX_RECOVERY_IDENTITY_MISMATCH")
            target = self._target(rel)
            if op_kind == "CREATE":
                if target.exists():
                    if not target.is_file() or _digest(target.read_bytes()) != after_sha:
                        raise WriteRecoveryError("CODEX_RECOVERY_CONCURRENT_CHANGE")
                    target.unlink()  # restore proven absence, never replace foreign bytes
            elif op_kind == "REPLACE":
                if not target.is_file():
                    raise WriteRecoveryError("CODEX_RECOVERY_TARGET_MISSING")
                current_sha = _digest(target.read_bytes())
                if current_sha == after_sha:
                    atomic_replace(target, bytes(before))
                elif current_sha != prior_sha:
                    raise WriteRecoveryError("CODEX_RECOVERY_CONCURRENT_CHANGE")
            else:
                raise WriteRecoveryError("CODEX_RECOVERY_KIND_UNKNOWN")
            self.db.execute(
                "UPDATE pending_edits SET state='ROLLED_BACK', before_data=X'' WHERE id=? AND state='PENDING'",
                (receipt,),
            )
            count += 1
        return count

    def confirm(self):
        """Finalize only after completed turn and current revision verification."""
        self._require_lock()
        latest = {}
        for receipt, root, rel, before, prior_sha, after_sha, op_kind in self._pending():
            if rel not in latest:
                latest[rel] = after_sha
        for rel, expected in latest.items():
            target = self._target(rel)
            if not target.is_file() or _digest(target.read_bytes()) != expected:
                raise WriteRecoveryError("CODEX_WRITE_CONVERGENCE_FAILED")
        self.db.execute(
            "UPDATE pending_edits SET state='CONFIRMED', before_data=X'' WHERE state='PENDING'"
        )

    def pending_count(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM pending_edits WHERE state='PENDING'"
        ).fetchone()[0]
