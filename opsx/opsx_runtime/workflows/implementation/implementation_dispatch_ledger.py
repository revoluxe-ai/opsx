"""Dispatch ledger verify-impl: переживает сброс LangGraph checkpoint.

SQLite: ``opsx/runs/verify/impl-<change_id>/dispatch.sqlite`` — учёт активных
mutating hop (stage/slice/run) независимо от ``checkpoints.sqlite``.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.repo_paths import resolve_opsx_repository_root

_SDK_TERMINAL = frozenset(
    {
        "FINISHED",
        "ERROR",
        "CANCELLED",
        "EXPIRED",
        "ORPHAN",
        "finished",
        "error",
        "cancelled",
        "expired",
        "orphan",
    }
)


def dispatch_ledger_path(change_id: str, *, repo_root: Path) -> Path:
    """Канонический путь ``dispatch.sqlite`` рядом с verify-impl run dir."""
    root = resolve_opsx_repository_root(repo_root)
    cid = str(change_id).strip()
    if not cid:
        raise ValueError("VERIFY_IMPL_CHANGE_ID_REQUIRED")
    return resource_path(root, "opsx", "runs", "verify", f"impl-{cid}", "dispatch.sqlite")


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class ActiveHopRecord:
    """Строка активного hop в dispatch ledger."""

    change_id: str
    slice_id: str | None
    stage: str
    agent_id: str | None
    run_id: str
    sdk_status: str
    updated_at: str
    primary_error: str | None


class ImplementationDispatchLedger:
    """SQLite-учёт in-flight mutating dispatch для verify-impl implementation lane."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS dispatch_hop ("
                "change_id TEXT NOT NULL, "
                "slice_id TEXT, "
                "stage TEXT NOT NULL, "
                "agent_id TEXT, "
                "run_id TEXT NOT NULL, "
                "sdk_status TEXT NOT NULL, "
                "updated_at TEXT NOT NULL, "
                "primary_error TEXT, "
                "PRIMARY KEY (change_id, run_id))"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_dispatch_hop_active "
                "ON dispatch_hop(change_id, stage, sdk_status)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.path), timeout=30)

    @staticmethod
    def _is_active_status(sdk_status: str) -> bool:
        return str(sdk_status or "").strip().upper() not in {
            s.upper() for s in _SDK_TERMINAL
        }

    def upsert_active_hop(
        self,
        *,
        change_id: str,
        slice_id: str | None,
        stage: str,
        agent_id: str | None,
        run_id: str,
        sdk_status: str,
        primary_error: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        """Записать или обновить hop; типично сразу после mutating send."""
        ts = updated_at or _utc_now_iso()
        with self._connect() as db:
            db.execute(
                "INSERT INTO dispatch_hop ("
                "change_id, slice_id, stage, agent_id, run_id, "
                "sdk_status, updated_at, primary_error"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(change_id, run_id) DO UPDATE SET "
                "slice_id=excluded.slice_id, "
                "stage=excluded.stage, "
                "agent_id=excluded.agent_id, "
                "sdk_status=excluded.sdk_status, "
                "updated_at=excluded.updated_at, "
                "primary_error=excluded.primary_error",
                (
                    change_id,
                    slice_id,
                    stage,
                    agent_id,
                    run_id,
                    sdk_status,
                    ts,
                    primary_error,
                ),
            )

    def clear_terminal(self, *, change_id: str, run_id: str, sdk_status: str) -> None:
        """Пометить hop терминальным после reconcile или durable outcome."""
        with self._connect() as db:
            db.execute(
                "UPDATE dispatch_hop SET sdk_status = ?, updated_at = ? "
                "WHERE change_id = ? AND run_id = ?",
                (sdk_status, _utc_now_iso(), change_id, run_id),
            )

    def get_conflicting_active(
        self,
        change_id: str,
        stage: str,
        *,
        exclude_run_id: str | None = None,
    ) -> list[ActiveHopRecord]:
        """Активные hop того же stage, блокирующие новый mutating dispatch."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT change_id, slice_id, stage, agent_id, run_id, "
                "sdk_status, updated_at, primary_error "
                "FROM dispatch_hop WHERE change_id = ? AND stage = ?",
                (change_id, stage),
            ).fetchall()
        out: list[ActiveHopRecord] = []
        for row in rows:
            rec = ActiveHopRecord(*row)
            if not self._is_active_status(rec.sdk_status):
                continue
            if exclude_run_id and rec.run_id == exclude_run_id:
                continue
            out.append(rec)
        return out

    def load_row(self, change_id: str, run_id: str) -> ActiveHopRecord | None:
        """Прочитать одну строку (для тестов и reconcile)."""
        with self._connect() as db:
            row = db.execute(
                "SELECT change_id, slice_id, stage, agent_id, run_id, "
                "sdk_status, updated_at, primary_error "
                "FROM dispatch_hop WHERE change_id = ? AND run_id = ?",
                (change_id, run_id),
            ).fetchone()
        if row is None:
            return None
        return ActiveHopRecord(*row)
