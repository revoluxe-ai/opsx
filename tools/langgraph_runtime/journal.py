"""Durable dispatch journal: never blind-retry an uncertain Cursor side effect."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping


def dispatch_key(run_id: str, change: str, assignment: Mapping[str, Any]) -> str:
    wire = json.dumps(
        {"run_id": run_id, "change": change, "assignment": assignment},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


class DispatchJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS dispatch ("
                "key TEXT PRIMARY KEY, run_id TEXT NOT NULL, change_id TEXT NOT NULL, "
                "status TEXT NOT NULL CHECK(status IN ('IN_FLIGHT','DONE')), "
                "result_json TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

    def _connect(self):
        return sqlite3.connect(str(self.path), timeout=30)

    def claim(self, run_id: str, change: str, assignment: Mapping[str, Any]):
        key = dispatch_key(run_id, change, assignment)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, result_json FROM dispatch WHERE key=?", (key,)
            ).fetchone()
            if row:
                status, wire = row
                return key, status, json.loads(wire) if wire else None
            db.execute(
                "INSERT INTO dispatch(key, run_id, change_id, status) "
                "VALUES(?,?,?,'IN_FLIGHT')",
                (key, run_id, change),
            )
            return key, "CLAIMED", None

    def complete(self, key: str, result: Mapping[str, Any]) -> None:
        wire = json.dumps(dict(result), ensure_ascii=False, default=str)
        with self._connect() as db:
            db.execute(
                "UPDATE dispatch SET status='DONE',result_json=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE key=? AND status='IN_FLIGHT'",
                (wire, key),
            )
            if db.total_changes != 1:
                raise RuntimeError("Dispatch journal completion lost or duplicated")

    def execute(self, backend: Any, run_id: str, change: str, assignment: Mapping[str, Any]):
        key, state, previous = self.claim(run_id, change, assignment)
        if state == "DONE":
            return previous
        if state == "IN_FLIGHT":
            # A prior process may have crashed after dispatching an external
            # side effect. Durable evidence is the only way to reconcile it.
            durable = backend.inspect_result(assignment)
            if durable is not None:
                recovered = backend.recovered_result(assignment, durable)
                self.complete(key, recovered)
                return recovered
            return {
                "completed": False,
                "uncertain": True,
                "durable_result_found": False,
                "required_check_id": assignment.get("required_check_id"),
                "expected_result_path": assignment.get("expected_result_path"),
                "executor": assignment.get("executor"),
                "started": False,
                "raw_output": "PREVIOUS_DISPATCH_IN_FLIGHT: no identity-bound evidence; "
                "refusing automatic re-execution",
            }
        # Persisted IN_FLIGHT *before* invoking a non-idempotent Cursor executor.
        try:
            outcome = dict(backend.execute_assigned(change, assignment))
        except Exception as exc:
            outcome = {
                "completed": False,
                "uncertain": True,
                "durable_result_found": False,
                "required_check_id": assignment.get("required_check_id"),
                "expected_result_path": assignment.get("expected_result_path"),
                "executor": assignment.get("executor"),
                "started": True,
                "raw_output": f"EXECUTOR_EXCEPTION: {type(exc).__name__}: {exc}",
            }
        self.complete(key, outcome)
        return outcome
