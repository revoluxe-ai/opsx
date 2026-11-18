"""Cursor-specific execution session; Cursor SDK bridge never escapes this provider."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import logging

from workflows.implementation.implementation_dispatch_ledger import (
    ImplementationDispatchLedger, dispatch_ledger_path,
)

from .cursor_bridge_runtime import CursorBridgeRuntime
from .cursor_sdk_adapter import CursorSDKAdapter
from .repair_cursor_sdk_adapter import RepairCursorSDKAdapter


class CursorExecutionSession:
    provider_name = "cursor"

    def __init__(
        self, *, workspace: Path, store_backend: str, store_scope: str,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self._bridge = CursorBridgeRuntime(
            self.workspace, store_backend=store_backend, store_scope=store_scope,
        )
        self._entered = False

    async def __aenter__(self) -> "CursorExecutionSession":
        await self._bridge.__aenter__()
        self._entered = True
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        try:
            return await self._bridge.__aexit__(exc_type, exc, tb)
        finally:
            self._entered = False

    def _require_started(self) -> None:
        if not self._entered:
            raise RuntimeError("EXECUTION_SESSION_NOT_STARTED")

    async def reconcile(
        self, change_id: str, workspace: Path, *, execution_id: str,
    ) -> None:
        self._require_started()
        if Path(workspace).resolve() != self.workspace:
            raise RuntimeError("EXECUTION_WORKSPACE_IDENTITY_MISMATCH")
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise RuntimeError("EXECUTION_IDENTITY_REQUIRED")
        # Cursor SDK index/GetRun, receipt persistence and identity-bound
        # ledger transitions belong to the provider, not the parent graph.
        from workflows.implementation.adapter_run_reconcile import (
            AdapterRunDisposition,
            reconcile_verify_impl_adapter_runs,
            store_reconcile_report,
        )

        report = await reconcile_verify_impl_adapter_runs(
            change_id, Path(workspace), self._bridge,
        )
        store_reconcile_report(execution_id, report)
        ledger = ImplementationDispatchLedger(
            dispatch_ledger_path(change_id, repo_root=Path(workspace)),
        )
        for row in report.rows:
            if row.disposition == AdapterRunDisposition.FINISHED:
                ledger.clear_terminal(
                    change_id=change_id,
                    run_id=row.record.run_id,
                    sdk_status=str(row.get_run_status or "FINISHED"),
                )
            elif row.disposition == AdapterRunDisposition.ORPHAN_CANDIDATE:
                ledger.clear_terminal(
                    change_id=change_id, run_id=row.record.run_id,
                    sdk_status="ORPHAN",
                )
        log = logging.getLogger(__name__)
        if report.error:
            log.warning(
                "verify_impl sdk_reconcile error=%s change=%s index=%s",
                report.error, change_id, report.index_db,
            )
        elif report.rows:
            summary = ", ".join(
                f"{r.record.run_id}:{r.disposition.value}" for r in report.rows[:5]
            )
            log.info(
                "verify_impl sdk_reconcile change=%s rows=%s %s",
                change_id, len(report.rows), summary,
            )

    def review_adapter(self, *, timeout_seconds: float) -> CursorSDKAdapter:
        self._require_started()
        return CursorSDKAdapter(
            timeout_seconds=timeout_seconds,
            allow_git_shell=False,
            bridge_runtime=self._bridge,
        )

    def repair_adapter(self, *, timeout_seconds: float) -> RepairCursorSDKAdapter:
        self._require_started()
        return RepairCursorSDKAdapter(
            timeout_seconds=timeout_seconds, bridge_runtime=self._bridge,
        )

    def model_selection(self) -> dict[str, Any]:
        self._require_started()
        from agents.adapters.hop_model_selection import default_hop_model_selection
        return default_hop_model_selection()
