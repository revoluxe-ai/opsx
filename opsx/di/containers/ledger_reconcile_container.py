"""Контейнер dispatch ledger и sdk reconcile при старте verify-impl (dependency-injector)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from dependency_injector import containers, providers

from opsx.di._runtime_imports import ensure_opsx_runtime_importable
from opsx.di.types import LedgerReconcileOutcome

if TYPE_CHECKING:
    from agents.adapters.cursor_bridge_runtime import CursorBridgeRuntime

_LOG = logging.getLogger(__name__)


async def _run_startup_reconcile(
    repo_root: Path,
    change_id: str,
    bridge: CursorBridgeRuntime,
    session_id: str,
) -> LedgerReconcileOutcome:
    """Выполнить reconcile и обновить ledger (как в ``run_verify_impl.execute``)."""
    ensure_opsx_runtime_importable(repo_root=repo_root)
    from workflows.implementation.implementation_dispatch_ledger import (
        ImplementationDispatchLedger,
        dispatch_ledger_path,
    )
    from workflows.implementation.adapter_run_reconcile import (
        SdkRunDisposition,
        reconcile_verify_impl_adapter_runs,
        store_reconcile_report,
    )

    cid = change_id.strip()
    if not cid:
        raise ValueError("VERIFY_IMPL_CHANGE_ID_REQUIRED")

    reconcile_report = await reconcile_verify_impl_adapter_runs(
        cid,
        repo_root,
        bridge,
    )
    store_reconcile_report(session_id, reconcile_report)
    ledger = ImplementationDispatchLedger(dispatch_ledger_path(cid, repo_root=repo_root))
    for row in reconcile_report.rows:
        if row.disposition == SdkRunDisposition.FINISHED:
            terminal = str(row.get_run_status or "FINISHED")
            ledger.clear_terminal(
                change_id=cid,
                run_id=row.record.run_id,
                sdk_status=terminal,
            )
        elif row.disposition == SdkRunDisposition.ORPHAN_CANDIDATE:
            ledger.clear_terminal(
                change_id=cid,
                run_id=row.record.run_id,
                sdk_status="ORPHAN",
            )
    if reconcile_report.error:
        _LOG.warning(
            "verify_impl sdk_reconcile error=%s change=%s index=%s",
            reconcile_report.error,
            cid,
            reconcile_report.index_db,
        )
    elif reconcile_report.rows:
        summary = ", ".join(
            f"{r.record.run_id}:{r.disposition.value}" for r in reconcile_report.rows[:5]
        )
        _LOG.info(
            "verify_impl sdk_reconcile change=%s rows=%s %s",
            cid,
            len(reconcile_report.rows),
            summary,
        )
    return LedgerReconcileOutcome(
        ledger=ledger,
        reconcile_report=reconcile_report,
        session_id=session_id,
    )


class LedgerReconcileContainer(containers.DeclarativeContainer):
    """Reconcile незавершённых SDK run и синхронизация ``ImplementationDispatchLedger``."""

    repo_root = providers.Object(Path())
    change_id = providers.Object("")

    startup = providers.Factory(
        _run_startup_reconcile,
        repo_root=repo_root,
        change_id=change_id,
        bridge=providers.Dependency(),
        session_id=providers.Dependency(),
    )
