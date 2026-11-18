"""Identity-bound durable Codex turn ledger. No provider replay is inferred from text.

A submitted turn is IN_FLIGHT until native thread/turn and host-evidence are
durably linked. On unknown outcome, a new parent dispatch fails closed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


class CodexTurnLedgerError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code + (": " + detail if detail else ""))


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_turn_ledger_path(root: Path) -> Path:
    location = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    identity = _sha(os.path.normcase(str(root.resolve())).encode("utf-8"))
    return location / "OPSX" / "codex-turn-ledger" / (identity + ".sqlite3")


def working_tree_revision(root: Path) -> str:
    """Bind a turn to HEAD and actual dirty input bytes, not Git status alone."""
    root = Path(root).resolve()
    def git(*args: str) -> bytes:
        try:
            p = subprocess.run(
                ["git", "-C", str(root), *args],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, timeout=25,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CodexTurnLedgerError("CODEX_REVISION_UNAVAILABLE") from exc
        if p.returncode != 0 or len(p.stdout) > 8_000_000:
            raise CodexTurnLedgerError("CODEX_REVISION_UNAVAILABLE")
        return p.stdout

    top = git("rev-parse", "--show-toplevel").decode("utf-8", "replace").strip()
    if os.path.normcase(str(Path(top).resolve())) != os.path.normcase(str(root)):
        raise CodexTurnLedgerError("CODEX_WORKSPACE_IDENTITY_MISMATCH")
    h = hashlib.sha256()
    h.update(str(root).encode("utf-8"))
    h.update(git("rev-parse", "HEAD"))
    h.update(git("status", "--porcelain=v1", "-uall", "-z"))
    names = git("ls-files", "-m", "-o", "--exclude-standard", "-z").split(b"\x00")
    if len(names) > 1002:
        raise CodexTurnLedgerError("CODEX_REVISION_TOO_MANY_CHANGED_PATHS")
    for token in sorted(set(n for n in names if n)):
        relative = token.decode("utf-8", "surrogateescape")
        candidate = root / relative
        h.update(b"\0PATH\0" + token)
        if candidate.is_symlink():
            raise CodexTurnLedgerError("CODEX_REVISION_SYMLINK_UNSUPPORTED")
        if candidate.is_file():
            if candidate.stat().st_size > 64_000_000:
                raise CodexTurnLedgerError("CODEX_REVISION_FILE_TOO_LARGE")
            with candidate.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
        elif candidate.is_dir() and (candidate / ".git").exists():
            # Git submodule or linked tree may have its own revision identity.
            child = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "HEAD"],
                capture_output=True, timeout=10,
            )
            if child.returncode != 0:
                raise CodexTurnLedgerError("CODEX_SUBMODULE_REVISION_UNKNOWN")
            h.update(child.stdout)
        else:
            h.update(b"\0MISSING\0")
    return h.hexdigest()


class CodexTurnLedger:
    def __init__(self, root: Path, *, path: Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.path = (path or default_turn_ledger_path(self.root)).resolve()
        if self.path.is_relative_to(self.root):
            raise CodexTurnLedgerError("CODEX_TURN_LEDGER_INSIDE_REPO")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=0.35, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=350")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS turns (
              operation_id TEXT PRIMARY KEY,
              workspace TEXT NOT NULL,
              execution_id TEXT NOT NULL,
              change_id TEXT NOT NULL,
              agent_key TEXT NOT NULL,
              input_revision TEXT NOT NULL,
              output_revision TEXT,
              model TEXT NOT NULL,
              tools_sha256 TEXT NOT NULL,
              prompt_sha256 TEXT NOT NULL,
              thread_id TEXT,
              turn_id TEXT,
              status TEXT NOT NULL CHECK(status IN
                ('IN_FLIGHT','COMPLETED','FAILED_SAFE','OUTCOME_UNKNOWN')),
              calls_json TEXT,
              result_sha256 TEXT,
              error_code TEXT,
              started_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
        """)
        self.db.execute("""
            CREATE INDEX IF NOT EXISTS idx_codex_workspace_status
            ON turns(workspace,status)
        """)

    def __enter__(self) -> "CodexTurnLedger":
        return self

    def __exit__(self, *_unused: Any) -> None:
        self.db.close()

    def _transaction(self, callback: Any) -> Any:
        try:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                result = callback()
                self.db.execute("COMMIT")
                return result
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
        except sqlite3.OperationalError as exc:
            raise CodexTurnLedgerError(
                "CODEX_TURN_SQLITE_BUSY" if "locked" in str(exc).lower()
                else "CODEX_TURN_SQLITE_OPERATION_FAILED"
            ) from exc

    def _uncertain(self) -> list[sqlite3.Row]:
        return self.db.execute(
            """SELECT operation_id,execution_id,change_id,agent_key,thread_id,
                      turn_id,status FROM turns WHERE workspace=?
                      AND status IN ('IN_FLIGHT','OUTCOME_UNKNOWN')
                      ORDER BY started_at""",
            (str(self.root),),
        ).fetchall()

    def reconcile(self) -> int:
        """No fabricated GetRun: unfinished ephemeral Codex turns always PAUSE."""
        unresolved = self._uncertain()
        if unresolved:
            one = unresolved[0]
            raise CodexTurnLedgerError(
                "CODEX_EXECUTION_OUTCOME_UNKNOWN",
                f"operation={one['operation_id']} execution={one['execution_id']} "
                f"thread={one['thread_id'] or '-'} turn={one['turn_id'] or '-'}; "
                "reconcile durable write and provider identity before retry",
            )
        row = self.db.execute(
            "SELECT count(*) FROM turns WHERE workspace=? AND status='COMPLETED'",
            (str(self.root),),
        ).fetchone()
        return int(row[0])

    def begin(
        self, *, execution_id: str, change_id: str, agent_key: str,
        model: str, tools: Sequence[str], prompt: str, input_revision: str,
    ) -> str:
        for value in (execution_id, change_id, agent_key, model, prompt, input_revision):
            if not isinstance(value, str) or not value.strip():
                raise CodexTurnLedgerError("CODEX_TURN_IDENTITY_MISSING")
        if len(input_revision) != 64 or not all(c in "0123456789abcdef" for c in input_revision):
            raise CodexTurnLedgerError("CODEX_REVISION_IDENTITY_INVALID")
        op_id = uuid.uuid4().hex
        tools_sha = _sha(json.dumps(sorted(set(tools))).encode("utf-8"))
        prompt_sha = _sha(prompt.encode("utf-8"))
        def insert() -> str:
            if self._uncertain():
                raise CodexTurnLedgerError("CODEX_EXECUTION_OUTCOME_UNKNOWN")
            already = self.db.execute(
                """SELECT operation_id FROM turns WHERE workspace=? AND execution_id=?
                AND agent_key=? AND input_revision=? AND prompt_sha256=?
                AND tools_sha256=? AND status='COMPLETED' LIMIT 1""",
                (str(self.root), execution_id, agent_key,
                 input_revision, prompt_sha, tools_sha),
            ).fetchone()
            if already:
                raise CodexTurnLedgerError(
                    "CODEX_DUPLICATE_TURN_DISPATCH", already[0],
                )
            now = _stamp()
            self.db.execute(
                """INSERT INTO turns(
                    operation_id,workspace,execution_id,change_id,agent_key,
                    input_revision,model,tools_sha256,prompt_sha256,status,
                    started_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,'IN_FLIGHT',?,?)""",
                (op_id, str(self.root), execution_id, change_id, agent_key,
                 input_revision, model, tools_sha, prompt_sha, now, now),
            )
            return op_id
        return self._transaction(insert)

    def require_resume(
        self, *, thread_id: str, execution_id: str, change_id: str,
        model: str, tools: Sequence[str], current_revision: str,
    ) -> dict[str, Any]:
        """Only resume the exact acknowledged, revision-matching native thread.

        A previous invocation of a different execution, workspace, model or
        tool set cannot grant authority to continue this one.
        """
        if not thread_id or not execution_id or not change_id:
            raise CodexTurnLedgerError("CODEX_RESUME_IDENTITY_MISSING")
        row=self.db.execute(
            """SELECT * FROM turns
               WHERE workspace=? AND thread_id=?
               ORDER BY updated_at DESC LIMIT 1""",
            (str(self.root),thread_id),
        ).fetchone()
        if row is None or row["status"] != "COMPLETED":
            raise CodexTurnLedgerError("CODEX_RESUME_THREAD_NOT_VERIFIED")
        required_tools=_sha(json.dumps(sorted(set(tools))).encode("utf-8"))
        if (
            row["execution_id"]!=execution_id
            or row["change_id"]!=change_id
            or row["model"]!=model
            or row["tools_sha256"]!=required_tools
            or row["output_revision"]!=current_revision
        ):
            raise CodexTurnLedgerError("CODEX_RESUME_AUTHORITY_OR_REVISION_MISMATCH")
        return dict(row)

    def _bind(self, operation_id: str, column: str, value: str) -> None:
        if column not in ("thread_id", "turn_id") or not value:
            raise CodexTurnLedgerError("CODEX_TURN_EVENT_INVALID")
        def update() -> None:
            row = self.db.execute(
                f"SELECT {column},status FROM turns WHERE operation_id=? AND workspace=?",
                (operation_id, str(self.root)),
            ).fetchone()
            if row is None or row["status"] != "IN_FLIGHT":
                raise CodexTurnLedgerError("CODEX_TURN_EVENT_STALE")
            if row[column] is not None and row[column] != value:
                raise CodexTurnLedgerError("CODEX_PROVIDER_IDENTITY_CONFLICT")
            self.db.execute(
                f"UPDATE turns SET {column}=?,updated_at=? WHERE operation_id=?",
                (value, _stamp(), operation_id),
            )
        self._transaction(update)

    def record_thread(self, operation_id: str, thread_id: str) -> None:
        self._bind(operation_id, "thread_id", thread_id)

    def record_turn(self, operation_id: str, turn_id: str) -> None:
        def check() -> None:
            row = self.db.execute(
                "SELECT thread_id FROM turns WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None or not row["thread_id"]:
                raise CodexTurnLedgerError("CODEX_THREAD_IDENTITY_REQUIRED")
        check()
        self._bind(operation_id, "turn_id", turn_id)

    def complete(
        self, operation_id: str, *, thread_id: str, turn_id: str,
        response: str, calls: Sequence[Mapping[str, Any]], output_revision: str,
    ) -> None:
        if not response or len(output_revision) != 64:
            raise CodexTurnLedgerError("CODEX_TURN_EVIDENCE_INCOMPLETE")
        def update() -> None:
            row = self.db.execute(
                "SELECT * FROM turns WHERE operation_id=? AND workspace=?",
                (operation_id, str(self.root)),
            ).fetchone()
            if row is None or row["status"] != "IN_FLIGHT":
                raise CodexTurnLedgerError("CODEX_TURN_COMPLETION_STALE")
            if row["thread_id"] != thread_id or row["turn_id"] != turn_id:
                raise CodexTurnLedgerError("CODEX_TURN_IDENTITY_MISMATCH")
            self.db.execute(
                """UPDATE turns SET status='COMPLETED',
                    calls_json=?,result_sha256=?,output_revision=?,updated_at=?
                    WHERE operation_id=?""",
                (json.dumps(list(calls),sort_keys=True),_sha(response.encode("utf-8")),
                 output_revision,_stamp(),operation_id),
            )
        self._transaction(update)

    def failure(self, operation_id: str, *, code: str, safe: bool) -> None:
        state = "FAILED_SAFE" if safe else "OUTCOME_UNKNOWN"
        def update() -> None:
            row = self.db.execute(
                "SELECT status FROM turns WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None or row["status"] != "IN_FLIGHT":
                raise CodexTurnLedgerError("CODEX_TURN_FAILURE_STALE")
            self.db.execute(
                "UPDATE turns SET status=?,error_code=?,updated_at=? WHERE operation_id=?",
                (state,code,_stamp(),operation_id),
            )
        self._transaction(update)

    def get(self, operation_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM turns WHERE operation_id=?", (operation_id,),
        ).fetchone()
        if row is None:
            raise CodexTurnLedgerError("CODEX_TURN_NOT_FOUND")
        return dict(row)