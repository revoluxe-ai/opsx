"""Codex app-server execution session, independent of Cursor SDK.

Codex turn tools are available; parent replay/reconcile has not been proven.
Do not produce an empty success report or substitute the Cursor bridge.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from contextlib import closing
import math
import sqlite3

from opsx_cli.codex_turn_ledger import CodexTurnLedger, CodexTurnLedgerError
from opsx_cli.codex_write_journal import CodexWriteJournal
from workflows.implementation.implementation_dispatch_ledger import dispatch_ledger_path

from .codex_adapter import CodexAppServerAdapter


class CodexExecutionSession:
    provider_name = "codex"

    def __init__(
        self, *, workspace: Path, store_backend: str, store_scope: str,
    ) -> None:
        if store_backend != "sqlite" or not store_scope:
            raise ValueError("CODEX_EXECUTION_STORE_SCOPE_INVALID")
        self.workspace = Path(workspace).resolve()
        self.store_scope = store_scope
        self._entered = False

    async def __aenter__(self) -> "CodexExecutionSession":
        if not self.workspace.is_dir():
            raise RuntimeError("CODEX_WORKSPACE_MISSING")
        self._entered = True
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self._entered = False

    def _require_started(self) -> None:
        if not self._entered:
            raise RuntimeError("EXECUTION_SESSION_NOT_STARTED")

    async def reconcile(
        self, change_id: str, workspace: Path, *, execution_id: str,
    ) -> None:
        """Reconcile provider and shared OPSX dispatch before opening a new hop.

        Completed turns are recorded, not automatically accepted as workflow
        evidence. Unknown mutating submissions or Cursor-active stages PAUSE.
        """
        self._require_started()
        if Path(workspace).resolve() != self.workspace:
            raise RuntimeError("EXECUTION_WORKSPACE_IDENTITY_MISMATCH")
        if not execution_id or not change_id:
            raise RuntimeError("CODEX_EXECUTION_IDENTITY_REQUIRED")
        if self.store_scope != "verify_impl:" + change_id:
            raise RuntimeError("CODEX_CHANGE_SCOPE_MISMATCH")

        # A previously accepted write whose durable turn acknowledgement was
        # lost must NOT be silently sent again. Recovery is separate and exact.
        with CodexTurnLedger(self.workspace) as turns:
            turns.reconcile()
        with CodexWriteJournal(self.workspace) as journal:
            if journal.pending_count():
                raise CodexTurnLedgerError("CODEX_PENDING_WRITE_RECOVERY_REQUIRED")

        # The original graph still has a provider-independent dispatch ledger.
        # Preserve its unresolved Cursor/other-provider runs instead of treating
        # their receipts as successful Codex evidence.
        path = dispatch_ledger_path(change_id, repo_root=self.workspace)
        if path.is_file():
            with closing(sqlite3.connect(
                "file:" + path.resolve().as_posix() + "?mode=ro",
                uri=True, timeout=0.35,
            )) as db:
                try:
                    rows = db.execute(
                        "SELECT run_id,sdk_status FROM dispatch_hop WHERE change_id=?",
                        (change_id,),
                    ).fetchall()
                except sqlite3.Error as exc:
                    raise CodexTurnLedgerError(
                        "CODEX_DISPATCH_LEDGER_SQLITE_BUSY"
                        if "locked" in str(exc).lower()
                        else "CODEX_DISPATCH_LEDGER_INVALID"
                    ) from exc
            terminal = {"FINISHED", "ERROR", "CANCELLED", "EXPIRED", "ORPHAN"}
            unresolved = [(run, status) for run, status in rows
                          if str(status).upper() not in terminal]
            if unresolved:
                raise CodexTurnLedgerError(
                    "CODEX_CROSS_PROVIDER_ACTIVE_DISPATCH",
                    "run=" + str(unresolved[0][0]) + " status=" + str(unresolved[0][1]),
                )

    @staticmethod
    def _per_turn_timeout(total_remaining_seconds: float) -> int:
        # Parent supervisor may run for hours. One app-server turn cannot;
        # Codex's 3600s safety cap must not reject a 3h parent workflow.
        try:
            seconds = float(total_remaining_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("CODEX_INVALID_HOP_TIME_BUDGET") from exc
        if not math.isfinite(seconds) or seconds < 1:
            raise ValueError("CODEX_INVALID_HOP_TIME_BUDGET")
        return min(3600, int(seconds))

    def review_adapter(self, *, timeout_seconds: float) -> CodexAppServerAdapter:
        self._require_started()
        return CodexAppServerAdapter(
            timeout_seconds=self._per_turn_timeout(timeout_seconds),
        )

    def repair_adapter(self, *, timeout_seconds: float) -> CodexAppServerAdapter:
        self._require_started()
        return CodexAppServerAdapter(
            timeout_seconds=self._per_turn_timeout(timeout_seconds),
        )

    def model_selection(self) -> dict[str, Any]:
        self._require_started()
        from opsx_cli.codex_provider import DEFAULT_CODEX_MODEL
        return {"id": DEFAULT_CODEX_MODEL}
