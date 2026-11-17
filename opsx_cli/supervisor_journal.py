"""Append-only журнал supervised workflow в SQLite (контроллер — единственный writer)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .storage import read_json, write_json

JOURNAL_SQLITE_NAME = "journal.sqlite"

_SCHEMA_SQL = (
    "CREATE TABLE IF NOT EXISTS journal_events ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
    "payload_json TEXT NOT NULL)"
)


def supervisor_journal_path(run_dir: Path) -> Path:
    """Канонический путь журнала супервизора в каталоге run."""
    return run_dir / JOURNAL_SQLITE_NAME


@dataclass
class JournalEntry:
    """Одна запись fix/commit цикла супервизора."""

    entry_id: str
    attempt: int
    fingerprint: str
    classification: dict[str, Any]
    status: str
    created_at: str
    fix_report: dict[str, Any] | None = None
    files_changed: list[dict[str, str]] = field(default_factory=list)
    checks_run: list[dict[str, Any]] = field(default_factory=list)
    commit_status: str | None = None
    commit_sha: str | None = None
    error: str | None = None
    change_id: str = ""


class SupervisorJournal:
    """Журнал ``opsx/runs/supervisor/<run_id>/journal.sqlite`` (WAL, короткая транзакция на событие)."""

    def __init__(self, path: Path):
        self.path = path
        if path.suffix not in {".sqlite", ".db"} and path.name != JOURNAL_SQLITE_NAME:
            raise ValueError(
                f"журнал супервизора должен быть SQLite ({JOURNAL_SQLITE_NAME}), получен: {path.name}",
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with _sqlite_connection(self.path, write=True) as conn:
            conn.execute(_SCHEMA_SQL)
            conn.commit()

    def append(self, entry: JournalEntry) -> None:
        """Дописать запись fix/commit в SQLite."""
        self.append_row({"record": "fix_commit", **asdict(entry)})

    def note(
        self,
        *,
        kind: str,
        what: str,
        why: str,
        **extra: Any,
    ) -> None:
        """Запись для разбора: что произошло, почему, и произвольный контекст (хвост лога)."""
        self.append_row({
            "record": "analysis",
            "recorded_at": self.now_iso(),
            "kind": kind,
            "what": what,
            "why": why,
            **extra,
        })

    def append_row(self, row: dict[str, Any]) -> None:
        """Одна INSERT-транзакция; reader на WAL не блокируется длительной записью."""
        wire = json.dumps(row, ensure_ascii=False)
        with _sqlite_connection(self.path, write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO journal_events (payload_json) VALUES (?)",
                (wire,),
            )
            conn.commit()

    def prior_dead_ends(self, change_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        """Сжатые тупики того же change по журналам всех run (SQLite; legacy ndjson только чтение)."""
        root = self.path.parent.parent
        if not root.is_dir():
            return []
        brief: list[dict[str, Any]] = []
        paths = sorted(root.glob(f"*/{JOURNAL_SQLITE_NAME}"))
        legacy = sorted(p for p in root.glob("*/journal.ndjson") if p not in paths)
        for path in [*paths, *legacy]:
            for row in _iter_journal_rows(path):
                if row.get("record") == "fix_commit":
                    status = str(row.get("status") or "")
                    if status not in {"FIX_REJECTED", "COMMIT_FAILED"}:
                        continue
                    if row.get("change_id") not in (None, "", change_id):
                        continue
                    report = row.get("fix_report") or {}
                    brief.append({
                        "run_id": path.parent.name,
                        "kind": status,
                        "what": str(report.get("summary") or report.get("verdict") or status)[:500],
                        "why": str(row.get("error") or "")[:1500],
                        "files": [f.get("path") for f in row.get("files_changed") or []][:20],
                        "code": (row.get("classification") or {}).get("code"),
                    })
                    continue
                if row.get("change_id") != change_id:
                    continue
                kind = str(row.get("kind") or "")
                if kind not in {"STOP", "ATTEMPT_FAIL", "EXCEPTION"}:
                    continue
                brief.append({
                    "run_id": row.get("run_id"),
                    "kind": kind,
                    "what": row.get("what"),
                    "why": str(row.get("why") or row.get("error") or "")[:1500],
                    "owner": row.get("owner"),
                    "code": row.get("code"),
                })
        return brief[-limit:]

    def list_entries(self) -> list[dict[str, Any]]:
        """Все события журнала текущего run (для панели и тестов)."""
        return list(_iter_journal_rows(self.path))

    def rows_since(self, after_id: int = 0) -> list[tuple[int, dict[str, Any]]]:
        """Хвост SQLite-журнала для SSE/статуса: ``(id, row)`` с ``id > after_id``."""
        if not self.path.is_file():
            return []
        rows: list[tuple[int, dict[str, Any]]] = []
        with _sqlite_connection(self.path, write=False) as conn:
            for row_id, wire in conn.execute(
                "SELECT id, payload_json FROM journal_events WHERE id > ? ORDER BY id",
                (after_id,),
            ):
                try:
                    rows.append((int(row_id), json.loads(wire)))
                except json.JSONDecodeError:
                    continue
        return rows

    @staticmethod
    def new_entry_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def write_snapshot(self, run_id: str, payload: dict[str, Any]) -> None:
        await write_json(self.path.parent / "snapshot.json", {"run_id": run_id, **payload})

    async def read_entry_sidecar(self, entry_id: str) -> dict[str, Any]:
        return await read_json(self.path.parent / "entries" / f"{entry_id}.json", {})


def find_journal_entry(journal_path: Path, entry_id: str) -> dict[str, Any]:
    """Найти запись fix_commit по ``entry_id`` (SQLite или legacy ndjson)."""
    if journal_path.suffix in {".sqlite", ".db"} or journal_path.name == JOURNAL_SQLITE_NAME:
        if not journal_path.is_file():
            return {}
        with _sqlite_connection(journal_path, write=False) as conn:
            row = conn.execute(
                "SELECT payload_json FROM journal_events "
                "WHERE json_extract(payload_json, '$.entry_id') = ? "
                "ORDER BY id DESC LIMIT 1",
                (entry_id,),
            ).fetchone()
            if row:
                parsed = json.loads(row[0])
                if isinstance(parsed, dict):
                    return parsed
    else:
        for line in _iter_ndjson_lines(journal_path):
            if line.get("entry_id") == entry_id:
                return line
    sidecar = journal_path.parent / "entries" / f"{entry_id}.json"
    if sidecar.is_file():
        return json.loads(sidecar.read_text(encoding="utf-8"))
    return {}


def _iter_journal_rows(path: Path) -> Iterator[dict[str, Any]]:
    if not path.is_file():
        return
    if path.suffix in {".sqlite", ".db"} or path.name == JOURNAL_SQLITE_NAME:
        loaded: list[dict[str, Any]] = []
        with _sqlite_connection(path, write=False) as conn:
            for (wire,) in conn.execute(
                "SELECT payload_json FROM journal_events ORDER BY id",
            ):
                try:
                    row = json.loads(wire)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    loaded.append(row)
        yield from loaded
        return
    yield from _iter_ndjson_lines(path)


@contextmanager
def _sqlite_connection(path: Path, *, write: bool):
    """Открыть SQLite с WAL; гарантированный ``close()`` на Windows."""
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        if not write:
            conn.execute("PRAGMA query_only=ON")
        yield conn
    finally:
        if write:
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
        conn.close()


def _iter_ndjson_lines(path: Path) -> Iterator[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            yield row