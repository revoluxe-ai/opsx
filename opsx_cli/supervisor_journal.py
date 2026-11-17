"""Append-only журнал supervised workflow в SQLite (контроллер — единственный writer)."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .storage import read_json, write_json

JOURNAL_SQLITE_NAME = "journal.sqlite"
JOURNAL_EXPORT_NAME = "supervisor-journal-export.sqlite"
JOURNAL_EXPORT_ZIP_NAME = "supervisor-journal-export.zip"

JOURNAL_SCHEMA_VERSION = 2

_SCHEMA_SQL = (
    "CREATE TABLE IF NOT EXISTS journal_events ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')), "
    "payload_json TEXT NOT NULL)"
)
_SCHEMA_META_SQL = (
    "CREATE TABLE IF NOT EXISTS journal_schema_meta ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), "
    "schema_version INTEGER NOT NULL, "
    "migrated_at TEXT NOT NULL)"
)
_NORMALIZED_SQL = (
    "CREATE TABLE IF NOT EXISTS journal_event_norm ("
    "event_id INTEGER PRIMARY KEY, "
    "incident_id TEXT, "
    "execution_id TEXT, "
    "attempt_id TEXT, "
    "repair_id TEXT, "
    "parent_event_id TEXT, "
    "event_seq INTEGER, "
    "failure_domain TEXT, "
    "causal_code TEXT, "
    "causal_signature TEXT, "
    "incident_phase TEXT, "
    "commit_sha TEXT, "
    "recorded_at TEXT, "
    "FOREIGN KEY(event_id) REFERENCES journal_events(id))"
)
_RECOVERY_CONTRACT_SQL = (
    "CREATE TABLE IF NOT EXISTS supervisor_recovery_contract ("
    "run_id TEXT PRIMARY KEY, "
    "incident_id TEXT NOT NULL, "
    "execution_id TEXT NOT NULL, "
    "original_causal_signature TEXT NOT NULL, "
    "before_failure_identity TEXT NOT NULL, "
    "repair_revision TEXT, "
    "replay_required INTEGER NOT NULL DEFAULT 1, "
    "replay_execution_id TEXT, "
    "replay_status TEXT NOT NULL DEFAULT 'PENDING_REPLAY', "
    "convergence_verdict TEXT, "
    "payload_json TEXT NOT NULL, "
    "updated_at TEXT NOT NULL)"
)


@dataclass(frozen=True, slots=True)
class RecoveryContract:
    """Durable recovery contract между commit repair и replay workflow."""

    run_id: str
    incident_id: str
    execution_id: str
    original_causal_signature: str
    before_failure_identity: str
    repair_revision: str | None
    replay_required: bool = True
    replay_execution_id: str | None = None
    replay_status: str = "PENDING_REPLAY"
    convergence_verdict: str | None = None

    def to_pending_replay(self) -> dict[str, Any]:
        """Форма для ``WorkflowSupervisor._pending_replay`` после restart."""
        return {
            "incident_id": self.incident_id,
            "execution_id": self.execution_id,
            "causal_signature": self.original_causal_signature,
            "before_failure_identity": self.before_failure_identity,
            "repair_revision": self.repair_revision,
            "replay_required": self.replay_required,
            "replay_execution_id": self.replay_execution_id,
            "replay_status": self.replay_status,
            "convergence_verdict": self.convergence_verdict,
        }


def supervisor_journal_path(run_dir: Path) -> Path:
    """Канонический путь журнала супервизора в каталоге run."""
    return run_dir / JOURNAL_SQLITE_NAME


def supervisor_runs_root(journal_path: Path) -> Path:
    """Каталог ``opsx/runs/supervisor`` для per-run ``journal.sqlite``."""
    if journal_path.name == JOURNAL_SQLITE_NAME:
        return journal_path.parent.parent
    return journal_path.parent


def _normalize_approach_text(text: str) -> str:
    collapsed = re.sub(r"\s+", " ", (text or "").strip().lower())
    return collapsed[:240]


def _insert_normalized_row(conn: sqlite3.Connection, event_id: int, row: dict[str, Any]) -> None:
    """Дополнительные колонки v2; historical rows без norm остаются только в payload_json."""
    cls = row.get("classification") if isinstance(row.get("classification"), dict) else {}
    conn.execute(
        "INSERT OR IGNORE INTO journal_event_norm ("
        "event_id, incident_id, execution_id, attempt_id, repair_id, parent_event_id, "
        "event_seq, failure_domain, causal_code, causal_signature, incident_phase, "
        "commit_sha, recorded_at"
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            event_id,
            str(row.get("incident_id") or row.get("run_id") or "") or None,
            str(row.get("execution_id") or row.get("run_id") or "") or None,
            str(row.get("attempt") or row.get("attempt_id") or "") or None,
            str(row.get("entry_id") or row.get("repair_id") or "") or None,
            str(row.get("parent_event_id") or "") or None,
            int(row.get("event_seq") or event_id),
            str(row.get("failure_domain") or cls.get("failure_domain") or "") or None,
            str(row.get("code") or cls.get("code") or "") or None,
            str(row.get("causal_signature") or cls.get("causal_signature") or "") or None,
            str(row.get("incident_phase") or row.get("status") or "") or None,
            str(row.get("commit_sha") or "") or None,
            str(row.get("recorded_at") or datetime.now(timezone.utc).isoformat()),
        ),
    )


def _fix_result_file_paths(fix_result: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for raw in fix_result.get("files_changed") or []:
        if isinstance(raw, str):
            paths.append(raw.replace("\\", "/"))
        elif isinstance(raw, dict):
            paths.append(str(raw.get("path") or "").replace("\\", "/"))
    return sorted(p for p in paths if p)


def repair_approach_key(fix_result: dict[str, Any]) -> str:
    """Стабильный ключ подхода: агент + файлы + краткое описание approach/root_cause."""
    agent = str(
        fix_result.get("repair_agent")
        or fix_result.get("agent_name")
        or ""
    ).strip().lower()
    approach = _normalize_approach_text(
        str(fix_result.get("approach") or fix_result.get("root_cause") or ""),
    )
    payload = {"agent": agent, "files": _fix_result_file_paths(fix_result), "approach": approach}
    wire = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()[:24]


def detect_repeat_repair(
    fix_result: dict[str, Any],
    prior_repairs: list[dict[str, Any]],
) -> bool:
    """True, если тот же агент, те же файлы и тот же подход уже пробовались для этого сбоя."""
    if not prior_repairs:
        return False
    key = repair_approach_key(fix_result)
    new_files = set(_fix_result_file_paths(fix_result))
    new_agent = str(fix_result.get("repair_agent") or fix_result.get("agent_name") or "").strip().lower()
    for prior in prior_repairs:
        if prior.get("approach_key") == key:
            return True
        prior_files = set(prior.get("files") or [])
        prior_agent = str(prior.get("repair_agent") or "").strip().lower()
        if new_files and prior_files == new_files and new_agent and new_agent == prior_agent:
            if _normalize_approach_text(str(prior.get("approach") or "")) == _normalize_approach_text(
                str(fix_result.get("approach") or fix_result.get("root_cause") or ""),
            ):
                return True
    return False


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
            conn.execute(_SCHEMA_META_SQL)
            conn.execute(_NORMALIZED_SQL)
            conn.execute(_RECOVERY_CONTRACT_SQL)
            row = conn.execute(
                "SELECT schema_version FROM journal_schema_meta WHERE id = 1",
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO journal_schema_meta (id, schema_version, migrated_at) "
                    "VALUES (1, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (JOURNAL_SCHEMA_VERSION,),
                )
            _backfill_normalized_rows(conn)
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
            cursor = conn.execute(
                "INSERT INTO journal_events (payload_json) VALUES (?)",
                (wire,),
            )
            event_id = int(cursor.lastrowid or 0)
            _insert_normalized_row(conn, event_id, row)
            conn.commit()

    def upsert_recovery_contract(self, contract: RecoveryContract) -> None:
        """Атомарно сохранить контракт replay (idempotent по ``run_id``)."""
        payload = contract.to_pending_replay()
        wire = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        updated = datetime.now(timezone.utc).isoformat()
        with _sqlite_connection(self.path, write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO supervisor_recovery_contract ("
                "run_id, incident_id, execution_id, original_causal_signature, "
                "before_failure_identity, repair_revision, replay_required, "
                "replay_execution_id, replay_status, convergence_verdict, payload_json, updated_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id) DO UPDATE SET "
                "incident_id=excluded.incident_id, "
                "execution_id=excluded.execution_id, "
                "original_causal_signature=excluded.original_causal_signature, "
                "before_failure_identity=excluded.before_failure_identity, "
                "repair_revision=excluded.repair_revision, "
                "replay_required=excluded.replay_required, "
                "replay_execution_id=excluded.replay_execution_id, "
                "replay_status=excluded.replay_status, "
                "convergence_verdict=excluded.convergence_verdict, "
                "payload_json=excluded.payload_json, "
                "updated_at=excluded.updated_at",
                (
                    contract.run_id,
                    contract.incident_id,
                    contract.execution_id,
                    contract.original_causal_signature,
                    contract.before_failure_identity,
                    contract.repair_revision,
                    1 if contract.replay_required else 0,
                    contract.replay_execution_id,
                    contract.replay_status,
                    contract.convergence_verdict,
                    wire,
                    updated,
                ),
            )
            conn.commit()

    def load_recovery_contract(self, run_id: str) -> RecoveryContract | None:
        """Восстановить активный replay-контракт для supervised run."""
        if not self.path.is_file():
            return None
        with _sqlite_connection(self.path, write=False) as conn:
            row = conn.execute(
                "SELECT run_id, incident_id, execution_id, original_causal_signature, "
                "before_failure_identity, repair_revision, replay_required, "
                "replay_execution_id, replay_status, convergence_verdict "
                "FROM supervisor_recovery_contract WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return RecoveryContract(
            run_id=str(row[0]),
            incident_id=str(row[1]),
            execution_id=str(row[2]),
            original_causal_signature=str(row[3]),
            before_failure_identity=str(row[4]),
            repair_revision=str(row[5]) if row[5] else None,
            replay_required=bool(int(row[6] or 0)),
            replay_execution_id=str(row[7]) if row[7] else None,
            replay_status=str(row[8] or "PENDING_REPLAY"),
            convergence_verdict=str(row[9]) if row[9] else None,
        )

    def clear_recovery_contract(self, run_id: str, *, convergence_verdict: str) -> None:
        """Закрыть контракт после convergence (оставить audit row с RESOLVED)."""
        with _sqlite_connection(self.path, write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE supervisor_recovery_contract SET "
                "replay_required = 0, replay_status = 'RESOLVED', "
                "convergence_verdict = ?, updated_at = ? "
                "WHERE run_id = ?",
                (
                    convergence_verdict,
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                ),
            )
            conn.commit()

    def prior_repairs(
        self,
        change_id: str,
        code: str,
        fingerprint: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """История repair-hop для того же change и того же кода/fingerprint по всем run."""
        root = supervisor_runs_root(self.path)
        if not root.is_dir():
            return []
        brief: list[dict[str, Any]] = []
        paths = sorted(root.glob(f"*/{JOURNAL_SQLITE_NAME}"))
        for path in paths:
            for row in _iter_journal_rows(path):
                if row.get("record") != "fix_commit":
                    continue
                if row.get("change_id") not in (None, "", change_id):
                    continue
                cls = row.get("classification") or {}
                row_fp = str(row.get("fingerprint") or cls.get("fingerprint") or "")
                row_code = str(cls.get("code") or "")
                if row_code != code and row_fp != fingerprint:
                    continue
                report = row.get("fix_report") if isinstance(row.get("fix_report"), dict) else {}
                files = sorted(
                    str(f.get("path") or "").replace("\\", "/")
                    for f in (row.get("files_changed") or [])
                    if isinstance(f, dict) and f.get("path")
                )
                approach = str(report.get("approach") or report.get("root_cause") or row.get("error") or "")[:500]
                brief.append({
                    "run_id": path.parent.name,
                    "entry_id": row.get("entry_id"),
                    "attempt": row.get("attempt"),
                    "status": row.get("status"),
                    "verdict": report.get("verdict"),
                    "code": row_code or code,
                    "fingerprint": row_fp or fingerprint,
                    "why_failed": str(row.get("error") or report.get("error") or "")[:1500],
                    "files": files,
                    "repair_agent": str(report.get("repair_agent") or ""),
                    "approach": approach,
                    "approach_key": repair_approach_key(report) if report else "",
                })
        return brief[-limit:]

    def prior_dead_ends(self, change_id: str, *, limit: int = 12) -> list[dict[str, Any]]:
        """Сжатые тупики того же change по журналам всех run (SQLite; legacy ndjson только чтение)."""
        root = supervisor_runs_root(self.path)
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


def _backfill_normalized_rows(conn: sqlite3.Connection) -> None:
    """Однократно заполнить ``journal_event_norm`` для legacy payload-only rows."""
    existing = conn.execute("SELECT COUNT(*) FROM journal_event_norm").fetchone()
    if existing is not None and int(existing[0]) > 0:
        return
    for event_id, wire in conn.execute(
        "SELECT id, payload_json FROM journal_events ORDER BY id",
    ):
        try:
            row = json.loads(wire)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            _insert_normalized_row(conn, int(event_id), row)


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


def _sqlite_backup_to(source: Path, dest: Path) -> None:
    """Онлайн-снимок одного SQLite через backup API; live-файл не усекается."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        dest.unlink()
    with _sqlite_connection(source, write=False) as src:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src.backup(dest_conn)
            dest_conn.commit()
        finally:
            dest_conn.close()


def _export_merged_supervisor_journals(supervisor_dir: Path, dest: Path) -> None:
    """Собрать все per-run журналы в один export-файл (только чтение live)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        dest.unlink()
    export = SupervisorJournal(dest)
    if not supervisor_dir.is_dir():
        return
    for run_dir in sorted(supervisor_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        live = run_dir / JOURNAL_SQLITE_NAME
        if not live.is_file():
            continue
        for row in _iter_journal_rows(live):
            export.append_row({**row, "export_run_id": run_dir.name})


def export_supervisor_journal(
    project_root: Path,
    *,
    run_id: str | None = None,
    dest: Path | None = None,
) -> Path:
    """Явный снимок журнала для Owner; не вызывается на каждое событие и не блокирует writer.

    Args:
        project_root: Корень product-репозитория.
        run_id: Если задан — backup одного run ``opsx/runs/supervisor/<run_id>/journal.sqlite``.
        dest: Путь export-файла; иначе ``opsx/runs/supervisor-journal-export.sqlite``.

    Returns:
        Абсолютный путь к созданному export-файлу.
    """
    runs = project_root / "opsx" / "runs"
    supervisor = runs / "supervisor"
    target = (dest or (runs / JOURNAL_EXPORT_NAME)).resolve()
    if run_id:
        live = supervisor / run_id / JOURNAL_SQLITE_NAME
        if not live.is_file():
            raise FileNotFoundError(live)
        _sqlite_backup_to(live, target)
    else:
        _export_merged_supervisor_journals(supervisor, target)
    return target


def export_supervisor_journal_zip(
    project_root: Path,
    *,
    run_id: str | None = None,
) -> Path:
    """ZIP-снимок repair-журнала: внутри ``supervisor-journal-export.sqlite`` (backup API).

    Live ``journal.sqlite`` не блокируется и не переключается на ndjson.
    """
    runs = project_root / "opsx" / "runs"
    sqlite_path = export_supervisor_journal(project_root, run_id=run_id)
    zip_path = (runs / JOURNAL_EXPORT_ZIP_NAME).resolve()
    if zip_path.is_file():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(sqlite_path, arcname=JOURNAL_EXPORT_NAME)
    return zip_path