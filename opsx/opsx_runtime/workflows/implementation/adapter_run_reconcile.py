"""Reconcile adapter runs для verify-impl без новых mutating send.

Читает ``index.db`` (read-only), опрашивает GetRun через существующий bridge
или ephemeral ``launch_bridge``, классифицирует RUNNING/orphan/FINISHED.
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import asyncio
import logging
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from collections.abc import Coroutine
from typing import Any, Literal

_RUNTIME_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = project_root()
_EXECUTOR_ROOT = resource_path(_REPO_ROOT, "tools", "cursor_executor")
if str(_EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXECUTOR_ROOT))

from cursor_executor.auth import resolve_auth  # noqa: E402

from workflows.implementation.implementation_dispatch_ledger import (  # noqa: E402
    ActiveHopRecord,
    ImplementationDispatchLedger,
    dispatch_ledger_path,
)
from workflows.implementation.adapter_store_context import (  # noqa: E402
    resolve_verify_impl_adapter_store,
)
from workflows.implementation.routing_contract import (  # noqa: E402
    format_current_implementation_procedure,
)
from workflows.repo_paths import resolve_opsx_repository_root  # noqa: E402

_LOG = logging.getLogger(__name__)

_SDK_DB_TERMINAL = frozenset({"FINISHED", "ERROR", "CANCELLED", "EXPIRED"})
_SDK_GET_RUN_TERMINAL = frozenset(
    {"finished", "error", "cancelled", "expired", "FINISHED", "ERROR", "CANCELLED", "EXPIRED"}
)

_DEFAULT_STALE_SECONDS = 180.0
_DEFAULT_RECENT_EVENT_SECONDS = 120.0
_DEFAULT_GET_RUN_TIMEOUT = 8.0

_GATE_ASYNC_EXECUTOR: ThreadPoolExecutor | None = None


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _orphan_reconcile_log_procedure(
    *,
    ledger: ImplementationDispatchLedger | None,
    change_id: str,
    run_id: str,
    hop: ActiveHopRecord | None = None,
) -> str | None:
    """``procedure`` для orphan allow INFO: stage+slice из ledger, без угадывания.

    Возвращает ``None``, если hop/run в ledger не найден (поле в логе опускаем).
    """
    if hop is not None:
        if str(hop.stage or "").strip():
            return format_current_implementation_procedure(hop.stage, hop.slice_id)
    if ledger is not None:
        row = ledger.load_row(change_id, run_id)
        if row is not None and str(row.stage or "").strip():
            return format_current_implementation_procedure(row.stage, row.slice_id)
    return None


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not str(ts).strip():
        return None
    text = str(ts).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class RunningRunRecord:
    """Строка ``runs`` со статусом RUNNING из ``index.db``."""

    run_id: str
    agent_id: str
    status: str
    updated_at: str
    last_event_at: str | None


class AdapterRunDisposition(str, Enum):
    """Исход reconcile для одного run."""

    FINISHED = "finished"
    ORPHAN_CANDIDATE = "orphan_candidate"
    RUNNING_LIVE = "running_live"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AdapterRunReconcileRow:
    """Результат классификации одного RUNNING run."""

    record: RunningRunRecord
    disposition: AdapterRunDisposition
    get_run_status: str | None = None
    detail: str | None = None


@dataclass
class AdapterRunReconcileReport:
    """Сводка reconcile по change."""

    change_id: str
    index_db: Path
    rows: list[AdapterRunReconcileRow] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Allow:
    """Разрешить новый mutating dispatch."""

    kind: Literal["allow"] = "allow"


@dataclass(frozen=True, slots=True)
class Pause:
    """Заблокировать mutating dispatch до reconcile."""

    reason: str
    kind: Literal["pause"] = "pause"


DispatchDecision = Allow | Pause

_RESUME_DISPATCH_EXCLUDE_CODES = frozenset(
    {
        "SDK_ACTIVE_RUN_CONFLICT",
        "SDK_BRIDGE_TIMEOUT",
        "DISPATCH_LEDGER_ACTIVE_HOP",
    },
)


def resolve_dispatch_exclude_run_id(
    *,
    stage: str,
    run_id: str | None,
    implementation_stage: str | None,
    implementation_status: str | None,
    implementation_recovery_code: str | None,
) -> str | None:
    """Run id для resume того же hop: не считать его конфликтом при dispatch gate."""
    rid = str(run_id or "").strip()
    if not rid:
        return None
    if str(implementation_stage or "").strip() != str(stage or "").strip():
        return None
    status = str(implementation_status or "").strip().upper()
    if status == "RUNNING":
        # Index/reconcile может отставать после только что завершённого hop (NO_CHANGE).
        return rid
    if status != "PAUSED":
        return None
    code = str(implementation_recovery_code or "").strip()
    if code not in _RESUME_DISPATCH_EXCLUDE_CODES:
        return None
    return rid


def _ledger_row_blocks_same_stage_dispatch(
    book: ImplementationDispatchLedger,
    *,
    change_id: str,
    run_id: str,
    stage: str,
) -> bool:
    """True, если ledger знает об активном hop на ``stage`` для ``run_id``."""
    row = book.load_row(change_id, run_id)
    if row is None:
        return True
    if not book._is_active_status(row.sdk_status):
        return False
    return str(row.stage or "").strip() == str(stage or "").strip()


def _reconcile_row_blocks_dispatch(
    row: AdapterRunReconcileRow,
    *,
    book: ImplementationDispatchLedger,
    change_id: str,
    stage: str,
    effective_exclude: str | None,
) -> bool:
    """True, если строка reconcile должна блокировать mutating dispatch на ``stage``."""
    if row.disposition == AdapterRunDisposition.ORPHAN_CANDIDATE:
        return False
    if row.disposition not in (
        AdapterRunDisposition.RUNNING_LIVE,
        AdapterRunDisposition.UNKNOWN,
    ):
        return False
    run_id = row.record.run_id
    if effective_exclude and run_id == effective_exclude:
        return False
    return _ledger_row_blocks_same_stage_dispatch(
        book,
        change_id=change_id,
        run_id=run_id,
        stage=stage,
    )


_RECONCILE_BY_SESSION: dict[str, AdapterRunReconcileReport] = {}


def store_reconcile_report(session_key: str, report: AdapterRunReconcileReport) -> None:
    """Сохранить отчёт reconcile на время одного verify-impl invocation (переживает hop)."""
    key = str(session_key or "").strip()
    if not key:
        return
    _RECONCILE_BY_SESSION[key] = report


def get_stored_reconcile_report(session_key: str) -> AdapterRunReconcileReport | None:
    """Вернуть отчёт, записанный composition root при старте workflow."""
    key = str(session_key or "").strip()
    if not key:
        return None
    return _RECONCILE_BY_SESSION.get(key)


def list_running_runs(index_db: Path) -> list[RunningRunRecord]:
    """Прочитать RUNNING runs из ``index.db`` (только чтение).

    Args:
        index_db: Путь к SQLite index durable adapter store.

    Returns:
        Список RUNNING; пустой, если файла нет или таблицы нет.
    """
    path = Path(index_db)
    if not path.is_file():
        return []
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        try:
            rows = db.execute(
                "SELECT r.run_id, r.agent_id, r.status, r.updated_at, "
                "(SELECT MAX(e.created_at) FROM run_events e WHERE e.run_id = r.run_id) "
                "FROM runs r WHERE UPPER(r.status) = 'RUNNING' "
                "ORDER BY r.updated_at ASC"
            ).fetchall()
        except sqlite3.Error as exc:
            _LOG.warning("list_running_runs sqlite_error path=%s err=%s", path, exc)
            return []
    return [
        RunningRunRecord(
            run_id=str(run_id),
            agent_id=str(agent_id),
            status=str(status),
            updated_at=str(updated_at),
            last_event_at=str(last_ev) if last_ev else None,
        )
        for run_id, agent_id, status, updated_at, last_ev in rows
    ]


def _classify_local_stale(
    record: RunningRunRecord,
    *,
    now: datetime,
    stale_after: timedelta,
    recent_event_after: timedelta,
) -> tuple[AdapterRunDisposition, str]:
    updated = _parse_iso(record.updated_at)
    last_ev = _parse_iso(record.last_event_at)
    stale_by_time = updated is not None and (now - updated) > stale_after
    no_recent_events = last_ev is None or (now - last_ev) > recent_event_after
    if stale_by_time and no_recent_events:
        return AdapterRunDisposition.ORPHAN_CANDIDATE, "stale_updated_at_no_recent_events"
    if not stale_by_time and (last_ev is not None and (now - last_ev) <= recent_event_after):
        return AdapterRunDisposition.RUNNING_LIVE, "recent_run_events"
    if stale_by_time:
        return AdapterRunDisposition.UNKNOWN, "stale_updated_at_but_recent_events"
    return AdapterRunDisposition.UNKNOWN, "running_not_stale"


async def _fetch_run_status(
    client: Any,
    *,
    run_id: str,
    workspace: Path,
    api_key: str,
) -> str:
    options = {"runtime": "local", "cwd": str(workspace.resolve()), "apiKey": api_key}
    timeout = _env_float("OPSX_SDK_GET_RUN_TIMEOUT", _DEFAULT_GET_RUN_TIMEOUT)
    run = await asyncio.wait_for(client.get_run(run_id, options), timeout=timeout)
    return str(getattr(run, "status", "") or "")


async def _classify_with_get_run(
    client: Any,
    record: RunningRunRecord,
    *,
    workspace: Path,
    api_key: str,
    now: datetime,
    stale_after: timedelta,
    recent_event_after: timedelta,
) -> AdapterRunReconcileRow:
    local_disp, local_detail = _classify_local_stale(
        record,
        now=now,
        stale_after=stale_after,
        recent_event_after=recent_event_after,
    )
    get_status: str | None = None
    try:
        get_status = await _fetch_run_status(
            client, run_id=record.run_id, workspace=workspace, api_key=api_key
        )
    except Exception as exc:
        _LOG.warning(
            "reconcile get_run failed run_id=%s err=%s", record.run_id, exc,
        )
        return AdapterRunReconcileRow(
            record=record,
            disposition=local_disp if local_disp != AdapterRunDisposition.UNKNOWN else AdapterRunDisposition.UNKNOWN,
            get_run_status=None,
            detail=f"get_run_error:{type(exc).__name__};{local_detail}",
        )

    if get_status in _SDK_GET_RUN_TERMINAL or get_status.upper() in _SDK_DB_TERMINAL:
        _LOG.info(
            "reconcile run terminal run_id=%s agent_id=%s status=%s",
            record.run_id,
            record.agent_id,
            get_status,
        )
        return AdapterRunReconcileRow(
            record=record,
            disposition=AdapterRunDisposition.FINISHED,
            get_run_status=get_status,
            detail="get_run_terminal",
        )

    norm = get_status.upper()
    if norm == "RUNNING":
        if local_disp == AdapterRunDisposition.ORPHAN_CANDIDATE:
            return AdapterRunReconcileRow(
                record=record,
                disposition=AdapterRunDisposition.ORPHAN_CANDIDATE,
                get_run_status=get_status,
                detail=local_detail,
            )
        if local_disp == AdapterRunDisposition.RUNNING_LIVE:
            return AdapterRunReconcileRow(
                record=record,
                disposition=AdapterRunDisposition.RUNNING_LIVE,
                get_run_status=get_status,
                detail=local_detail,
            )
        return AdapterRunReconcileRow(
            record=record,
            disposition=AdapterRunDisposition.UNKNOWN,
            get_run_status=get_status,
            detail=local_detail,
        )

    return AdapterRunReconcileRow(
        record=record,
        disposition=AdapterRunDisposition.UNKNOWN,
        get_run_status=get_status,
        detail=f"unmapped_get_run_status:{get_status}",
    )


async def reconcile_verify_impl_adapter_runs(
    change_id: str,
    repo_root: Path,
    bridge: Any | None,
) -> AdapterRunReconcileReport:
    """Сверить RUNNING rows в store с GetRun (без mutating send).

    Args:
        change_id: OpenSpec change id.
        repo_root: Корень репозитория.
        bridge: Опциональный ``CursorBridgeRuntime`` (``connection()``); иначе ephemeral bridge.

    Returns:
        Отчёт с классификацией по каждому RUNNING в index.
    """
    ctx = resolve_verify_impl_adapter_store(change_id, repo_root=repo_root)
    report = AdapterRunReconcileReport(change_id=ctx.change_id, index_db=ctx.index_db)
    records = list_running_runs(ctx.index_db)
    if not records:
        return report

    auth = resolve_auth()
    if not auth.api_key:
        report.error = "CURSOR_API_KEY_MISSING"
        for rec in records:
            report.rows.append(
                AdapterRunReconcileRow(
                    record=rec,
                    disposition=AdapterRunDisposition.UNKNOWN,
                    detail="no_api_key",
                )
            )
        return report

    stale_after = timedelta(seconds=_env_float("OPSX_SDK_RUN_STALE_SECONDS", _DEFAULT_STALE_SECONDS))
    recent_event_after = timedelta(
        seconds=_env_float("OPSX_SDK_RECENT_EVENT_SECONDS", _DEFAULT_RECENT_EVENT_SECONDS)
    )
    now = datetime.now(UTC)

    async def _reconcile_with_client(client: Any) -> None:
        for rec in records:
            row = await _classify_with_get_run(
                client,
                rec,
                workspace=ctx.repo_root,
                api_key=auth.api_key,
                now=now,
                stale_after=stale_after,
                recent_event_after=recent_event_after,
            )
            report.rows.append(row)

    if bridge is not None and getattr(bridge, "_started", False):
        async with bridge.connection(ctx.repo_root) as client:
            await _reconcile_with_client(client)
        return report

    from agents.adapters.reconcile_async_client import launch_bridge_client  # noqa: WPS433

    launch = ctx.bridge_launch_kwargs()
    async with await launch_bridge_client(**launch) as client:
        await _reconcile_with_client(client)
    return report


def _gate_async_executor() -> ThreadPoolExecutor:
    global _GATE_ASYNC_EXECUTOR
    if _GATE_ASYNC_EXECUTOR is None:
        _GATE_ASYNC_EXECUTOR = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="sdk-dispatch-gate",
        )
    return _GATE_ASYNC_EXECUTOR


def _bridge_for_sync_dispatch_gate(bridge: Any | None) -> Any | None:
    """Не передавать live bridge в worker-thread asyncio.run — клиент привязан к root loop."""
    if bridge is None:
        return None
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return bridge
    return None


def _run_async_gate_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    """Выполнить async GetRun gate на выделенном loop (без reuse закрытого root loop)."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            _ = loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)


def _run_gate_coroutine(coro: Coroutine[Any, Any, DispatchDecision]) -> DispatchDecision:
    """Выполнить async GetRun gate из sync graph node (отдельный loop в worker thread)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run_async_gate_coro(coro)
    # LangGraph hop already on running loop — nested asyncio.run deadlock.
    timeout = _env_float("OPSX_SDK_GET_RUN_TIMEOUT", _DEFAULT_GET_RUN_TIMEOUT)
    per_record = timeout + 2.0
    budget = max(per_record * 4, 30.0)
    future = _gate_async_executor().submit(_run_async_gate_coro, coro)
    return future.result(timeout=budget)


async def _decide_nonempty_reconcile_via_get_run(
    *,
    change_id: str,
    repo_root: Path,
    reconcile: AdapterRunReconcileReport,
    ledger: ImplementationDispatchLedger,
    stage: str,
    effective_exclude: str | None,
    bridge: Any | None = None,
) -> DispatchDecision:
    """Повторный bounded GetRun для строк session reconcile (после долгих plan hop)."""
    root = resolve_opsx_repository_root(repo_root)
    ctx = resolve_verify_impl_adapter_store(change_id, repo_root=root)
    auth = resolve_auth()
    if not auth.api_key:
        for row in reconcile.rows:
            if _reconcile_row_blocks_dispatch(
                row,
                book=ledger,
                change_id=change_id,
                stage=stage,
                effective_exclude=effective_exclude,
            ):
                rec = row.record
                return Pause(
                    reason=(
                        f"sdk_reconcile_block run_id={rec.run_id} "
                        f"disposition={row.disposition.value} detail={row.detail or '-'}"
                    ),
                )
        return Allow()

    stale_after = timedelta(seconds=_env_float("OPSX_SDK_RUN_STALE_SECONDS", _DEFAULT_STALE_SECONDS))
    recent_event_after = timedelta(
        seconds=_env_float("OPSX_SDK_RECENT_EVENT_SECONDS", _DEFAULT_RECENT_EVENT_SECONDS)
    )
    now = datetime.now(UTC)

    async def _scan(client: Any) -> DispatchDecision:
        for row in reconcile.rows:
            if row.disposition == AdapterRunDisposition.ORPHAN_CANDIDATE:
                ledger.clear_terminal(
                    change_id=change_id,
                    run_id=row.record.run_id,
                    sdk_status="ORPHAN",
                )
                procedure = _orphan_reconcile_log_procedure(
                    ledger=ledger,
                    change_id=change_id,
                    run_id=row.record.run_id,
                )
                if procedure is not None:
                    _LOG.info(
                        "reconcile orphan allow run_id=%s agent_id=%s "
                        "procedure=%s detail=%s",
                        row.record.run_id,
                        row.record.agent_id,
                        procedure,
                        row.detail or "-",
                    )
                else:
                    _LOG.info(
                        "reconcile orphan allow run_id=%s agent_id=%s detail=%s",
                        row.record.run_id,
                        row.record.agent_id,
                        row.detail or "-",
                    )
                continue
            if row.disposition == AdapterRunDisposition.FINISHED:
                ledger.clear_terminal(
                    change_id=change_id,
                    run_id=row.record.run_id,
                    sdk_status=str(row.get_run_status or "FINISHED"),
                )
                continue
            if row.disposition not in (
                AdapterRunDisposition.RUNNING_LIVE,
                AdapterRunDisposition.UNKNOWN,
            ):
                continue
            refreshed = await _classify_with_get_run(
                client,
                row.record,
                workspace=ctx.repo_root,
                api_key=auth.api_key,
                now=now,
                stale_after=stale_after,
                recent_event_after=recent_event_after,
            )
            if refreshed.disposition == AdapterRunDisposition.FINISHED:
                ledger.clear_terminal(
                    change_id=change_id,
                    run_id=row.record.run_id,
                    sdk_status=str(refreshed.get_run_status or "FINISHED"),
                )
                _LOG.info(
                    "session_reconcile stale terminal run_id=%s get_run_status=%s",
                    row.record.run_id,
                    refreshed.get_run_status,
                )
                continue
            if refreshed.disposition == AdapterRunDisposition.ORPHAN_CANDIDATE:
                ledger.clear_terminal(
                    change_id=change_id,
                    run_id=row.record.run_id,
                    sdk_status="ORPHAN",
                )
                continue
            candidate = AdapterRunReconcileRow(
                record=row.record,
                disposition=refreshed.disposition,
                get_run_status=refreshed.get_run_status,
                detail=refreshed.detail,
            )
            if not _reconcile_row_blocks_dispatch(
                candidate,
                book=ledger,
                change_id=change_id,
                stage=stage,
                effective_exclude=effective_exclude,
            ):
                continue
            return Pause(
                reason=(
                    f"sdk_reconcile_block run_id={row.record.run_id} "
                    f"disposition={refreshed.disposition.value} "
                    f"detail={refreshed.detail or '-'}"
                ),
            )
        return Allow()

    try:
        if bridge is not None and getattr(bridge, "_started", False):
            async with bridge.connection(ctx.repo_root) as client:
                return await _scan(client)
        from agents.adapters.reconcile_async_client import launch_bridge_client  # noqa: WPS433

        launch = ctx.bridge_launch_kwargs()
        async with await launch_bridge_client(**launch) as client:
            return await _scan(client)
    except Exception as exc:
        _LOG.warning("nonempty_reconcile get_run bridge_error err=%s", exc)
        for row in reconcile.rows:
            if _reconcile_row_blocks_dispatch(
                row,
                book=ledger,
                change_id=change_id,
                stage=stage,
                effective_exclude=effective_exclude,
            ):
                rec = row.record
                return Pause(
                    reason=(
                        f"sdk_reconcile_block run_id={rec.run_id} "
                        f"disposition={row.disposition.value} detail={row.detail or '-'}"
                    ),
                )
        return Allow()


async def _decide_empty_reconcile_via_get_run(
    *,
    change_id: str,
    repo_root: Path,
    stage: str,
    ledger: ImplementationDispatchLedger | None = None,
    bridge: Any | None = None,
    exclude_run_id: str | None = None,
) -> DispatchDecision:
    """Пустой reconcile: ledger уже проверен; index RUNNING — только через bounded GetRun."""
    root = resolve_opsx_repository_root(repo_root)
    ctx = resolve_verify_impl_adapter_store(change_id, repo_root=root)
    records = list_running_runs(ctx.index_db)
    if not records:
        return Allow()

    auth = resolve_auth()
    if not auth.api_key:
        rec = records[0]
        return Pause(
            reason=(
                f"sdk_index_get_run_unavailable no_api_key "
                f"run_id={rec.run_id} agent_id={rec.agent_id}"
            ),
        )

    stale_after = timedelta(seconds=_env_float("OPSX_SDK_RUN_STALE_SECONDS", _DEFAULT_STALE_SECONDS))
    recent_event_after = timedelta(
        seconds=_env_float("OPSX_SDK_RECENT_EVENT_SECONDS", _DEFAULT_RECENT_EVENT_SECONDS)
    )
    now = datetime.now(UTC)

    async def _scan(client: Any) -> DispatchDecision:
        for rec in records:
            if exclude_run_id and rec.run_id == exclude_run_id:
                continue
            if ledger is not None and not _ledger_row_blocks_same_stage_dispatch(
                ledger,
                change_id=change_id,
                run_id=rec.run_id,
                stage=stage,
            ):
                continue
            row = await _classify_with_get_run(
                client,
                rec,
                workspace=ctx.repo_root,
                api_key=auth.api_key,
                now=now,
                stale_after=stale_after,
                recent_event_after=recent_event_after,
            )
            if row.disposition == AdapterRunDisposition.FINISHED:
                if ledger is not None:
                    ledger.clear_terminal(
                        change_id=change_id,
                        run_id=rec.run_id,
                        sdk_status=str(row.get_run_status or "FINISHED"),
                    )
                _LOG.info(
                    "empty_reconcile index stale terminal run_id=%s get_run_status=%s",
                    rec.run_id,
                    row.get_run_status,
                )
                continue
            if row.disposition == AdapterRunDisposition.ORPHAN_CANDIDATE:
                if ledger is not None:
                    ledger.clear_terminal(
                        change_id=change_id,
                        run_id=rec.run_id,
                        sdk_status="ORPHAN",
                    )
                procedure = _orphan_reconcile_log_procedure(
                    ledger=ledger,
                    change_id=change_id,
                    run_id=rec.run_id,
                )
                if procedure is not None:
                    _LOG.info(
                        "empty_reconcile orphan allow run_id=%s agent_id=%s "
                        "procedure=%s detail=%s",
                        rec.run_id,
                        rec.agent_id,
                        procedure,
                        row.detail or "-",
                    )
                else:
                    _LOG.info(
                        "empty_reconcile orphan allow run_id=%s agent_id=%s detail=%s",
                        rec.run_id,
                        rec.agent_id,
                        row.detail or "-",
                    )
                continue
            if row.disposition in (
                AdapterRunDisposition.RUNNING_LIVE,
                AdapterRunDisposition.UNKNOWN,
            ):
                return Pause(
                    reason=(
                        f"sdk_index_get_run_block run_id={rec.run_id} "
                        f"agent_id={rec.agent_id} "
                        f"disposition={row.disposition.value} "
                        f"detail={row.detail or '-'}"
                    ),
                )
        return Allow()

    try:
        if bridge is not None and getattr(bridge, "_started", False):
            async with bridge.connection(ctx.repo_root) as client:
                return await _scan(client)
        from agents.adapters.reconcile_async_client import launch_bridge_client  # noqa: WPS433

        launch = ctx.bridge_launch_kwargs()
        async with await launch_bridge_client(**launch) as client:
            return await _scan(client)
    except Exception as exc:
        rec = records[0]
        _LOG.warning("empty_reconcile get_run bridge_error err=%s", exc)
        return Pause(
            reason=(
                f"sdk_index_get_run_unavailable bridge_error={type(exc).__name__} "
                f"run_id={rec.run_id} agent_id={rec.agent_id}"
            ),
        )


async def _filter_ledger_conflicts_via_get_run(
    *,
    change_id: str,
    repo_root: Path,
    conflicts: list[ActiveHopRecord],
    ledger: ImplementationDispatchLedger,
    bridge: Any | None = None,
) -> list[ActiveHopRecord]:
    """Снять ledger RUNNING, если bounded GetRun доказал терминальность."""
    if not conflicts:
        return []
    root = resolve_opsx_repository_root(repo_root)
    ctx = resolve_verify_impl_adapter_store(change_id, repo_root=root)
    auth = resolve_auth()
    if not auth.api_key:
        return conflicts

    stale_after = timedelta(seconds=_env_float("OPSX_SDK_RUN_STALE_SECONDS", _DEFAULT_STALE_SECONDS))
    recent_event_after = timedelta(
        seconds=_env_float("OPSX_SDK_RECENT_EVENT_SECONDS", _DEFAULT_RECENT_EVENT_SECONDS)
    )
    now = datetime.now(UTC)

    async def _scan(client: Any) -> list[ActiveHopRecord]:
        still_active: list[ActiveHopRecord] = []
        for conflict in conflicts:
            record = RunningRunRecord(
                run_id=conflict.run_id,
                agent_id=str(conflict.agent_id or ""),
                status="RUNNING",
                updated_at=conflict.updated_at,
                last_event_at=None,
            )
            row = await _classify_with_get_run(
                client,
                record,
                workspace=ctx.repo_root,
                api_key=auth.api_key,
                now=now,
                stale_after=stale_after,
                recent_event_after=recent_event_after,
            )
            if row.disposition == AdapterRunDisposition.FINISHED:
                terminal = str(row.get_run_status or "FINISHED")
                ledger.clear_terminal(
                    change_id=change_id,
                    run_id=conflict.run_id,
                    sdk_status=terminal,
                )
                continue
            if row.disposition == AdapterRunDisposition.ORPHAN_CANDIDATE:
                ledger.clear_terminal(
                    change_id=change_id,
                    run_id=conflict.run_id,
                    sdk_status="ORPHAN",
                )
                procedure = _orphan_reconcile_log_procedure(
                    ledger=ledger,
                    change_id=change_id,
                    run_id=conflict.run_id,
                    hop=conflict,
                )
                if procedure is not None:
                    _LOG.info(
                        "ledger_conflict orphan cleared run_id=%s agent_id=%s "
                        "procedure=%s",
                        conflict.run_id,
                        conflict.agent_id,
                        procedure,
                    )
                else:
                    _LOG.info(
                        "ledger_conflict orphan cleared run_id=%s agent_id=%s",
                        conflict.run_id,
                        conflict.agent_id,
                    )
                continue
            if row.disposition in (
                AdapterRunDisposition.RUNNING_LIVE,
                AdapterRunDisposition.UNKNOWN,
            ):
                still_active.append(conflict)
        return still_active

    try:
        if bridge is not None and getattr(bridge, "_started", False):
            async with bridge.connection(ctx.repo_root) as client:
                return await _scan(client)
        from agents.adapters.reconcile_async_client import launch_bridge_client  # noqa: WPS433

        launch = ctx.bridge_launch_kwargs()
        async with await launch_bridge_client(**launch) as client:
            return await _scan(client)
    except Exception as exc:
        _LOG.warning("ledger_conflict_get_run bridge_error err=%s", exc)
        return conflicts


def decide_mutating_dispatch_allowed(
    *,
    change_id: str,
    stage: str,
    repo_root: Path,
    reconcile: AdapterRunReconcileReport | None = None,
    ledger: ImplementationDispatchLedger | None = None,
    exclude_run_id: str | None = None,
    bridge: Any | None = None,
) -> DispatchDecision:
    """Fail-closed gate перед новым mutating hop.

    Args:
        change_id: OpenSpec change id.
        stage: Implementation stage (например ``APPLY_RED``).
        repo_root: Корень репозитория.
        reconcile: Предзагруженный отчёт GetRun; ``rows=[]`` — local index + ledger
            (без повторного GetRun в graph).
        ledger: Ledger instance; по умолчанию открывается по каноническому пути.
        exclude_run_id: Исключить свой run при resume той же идентичности.
        bridge: Опциональный live ``CursorBridgeRuntime`` для bounded GetRun.

    Returns:
        ``Allow`` или ``Pause(reason)``.
    """
    root = resolve_opsx_repository_root(repo_root)
    book = ledger or ImplementationDispatchLedger(dispatch_ledger_path(change_id, repo_root=root))

    effective_exclude = exclude_run_id
    if effective_exclude:
        row = book.load_row(change_id, effective_exclude)
        if row is None or not book._is_active_status(row.sdk_status):
            effective_exclude = None
        elif reconcile is not None:
            for rec in reconcile.rows:
                if (
                    rec.record.run_id == effective_exclude
                    and rec.disposition == AdapterRunDisposition.FINISHED
                ):
                    effective_exclude = None
                    break

    conflicts = book.get_conflicting_active(
        change_id,
        stage,
        exclude_run_id=effective_exclude,
    )
    if conflicts and reconcile is not None and reconcile.rows:
        unresolved: list[Any] = []
        for conflict in conflicts:
            match = next(
                (
                    row
                    for row in reconcile.rows
                    if row.record.run_id == conflict.run_id
                    and row.disposition
                    in (
                        AdapterRunDisposition.FINISHED,
                        AdapterRunDisposition.ORPHAN_CANDIDATE,
                    )
                ),
                None,
            )
            if match is not None:
                if match.disposition == AdapterRunDisposition.ORPHAN_CANDIDATE:
                    terminal = "ORPHAN"
                else:
                    terminal = str(match.get_run_status or "FINISHED")
                book.clear_terminal(
                    change_id=change_id,
                    run_id=conflict.run_id,
                    sdk_status=terminal,
                )
                continue
            unresolved.append(conflict)
        conflicts = unresolved
    gate_bridge = _bridge_for_sync_dispatch_gate(bridge)
    if conflicts:
        verified = _run_gate_coroutine(
            _filter_ledger_conflicts_via_get_run(
                change_id=change_id,
                repo_root=root,
                conflicts=conflicts,
                ledger=book,
                bridge=gate_bridge,
            )
        )
        if verified:
            ids = ", ".join(c.run_id for c in verified[:3])
            return Pause(
                reason=f"dispatch_ledger_active_hop stage={stage} run_ids={ids}",
            )

    # Пустой reconcile — ledger уже проверен; RUNNING в index только через bounded GetRun
    # (не local stale / время). Непустые rows — authoritative reconcile_verify_impl_adapter_runs.
    if reconcile is None or not reconcile.rows:
        return _run_gate_coroutine(
            _decide_empty_reconcile_via_get_run(
                change_id=change_id,
                repo_root=root,
                stage=stage,
                ledger=book,
                bridge=gate_bridge,
                exclude_run_id=effective_exclude,
            )
        )

    return _run_gate_coroutine(
        _decide_nonempty_reconcile_via_get_run(
            change_id=change_id,
            repo_root=root,
            reconcile=reconcile,
            ledger=book,
            stage=stage,
            effective_exclude=effective_exclude,
            bridge=gate_bridge,
        )
    )
