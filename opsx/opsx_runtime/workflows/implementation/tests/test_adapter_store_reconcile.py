"""Тесты sdk store context, reconcile stale detection и dispatch ledger."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from workflows.implementation.implementation_dispatch_ledger import (
    ImplementationDispatchLedger,
    dispatch_ledger_path,
)
from workflows.implementation.adapter_run_reconcile import (
    Allow,
    Pause,
    RunningRunRecord,
    AdapterRunReconcileReport,
    AdapterRunDisposition,
    AdapterRunReconcileRow,
    _run_async_gate_coro,
    decide_mutating_dispatch_allowed,
    list_running_runs,
    reconcile_verify_impl_adapter_runs,
    resolve_dispatch_exclude_run_id,
)
from workflows.implementation.adapter_store_context import (
    resolve_verify_impl_adapter_store,
)


def _register_reconcile_bridge_hooks(
    *,
    fake_client_cls: type,
) -> None:
    """Register test doubles for provider reconcile hooks (no provider package import)."""
    from agents.adapters import provider_registry

    async def launch_bridge_client(**kwargs: object) -> object:
        return await fake_client_cls.launch_bridge(**kwargs)

    provider_registry.register_reconcile_hook("launch_bridge_client", launch_bridge_client)
    provider_registry.register_reconcile_hook(
        "async_client_type",
        lambda: fake_client_cls,
    )


def _ensure_index_schema(db_path: Path) -> None:
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                workspace_ref TEXT NOT NULL,
                status TEXT NOT NULL,
                active_run_id TEXT,
                latest_checkpoint_ref_json TEXT,
                name TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                request_id TEXT,
                agent_id TEXT NOT NULL,
                turn_number INTEGER NOT NULL,
                status TEXT NOT NULL,
                model TEXT,
                model_params_json TEXT,
                start_checkpoint_ref_json TEXT,
                latest_checkpoint_ref_json TEXT,
                error_code TEXT,
                usage_ref TEXT,
                usage_json TEXT,
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                cancelled_at TEXT,
                expired_at TEXT
            );
            CREATE TABLE IF NOT EXISTS run_events (
                run_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                offset TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT,
                payload_ref TEXT,
                idempotency_key TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            );
            """
        )


def _patch_get_run_bridge(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: str,
) -> AsyncMock:
    auth = MagicMock()
    auth.api_key = "test-key"
    monkeypatch.setattr(
        "workflows.implementation.adapter_run_reconcile.resolve_auth",
        lambda: auth,
    )
    client = AsyncMock()
    run_obj = MagicMock()
    run_obj.status = status
    client.get_run = AsyncMock(return_value=run_obj)

    class _FakeClient:
        @staticmethod
        async def launch_bridge(**_kwargs):
            class _Mgr:
                async def __aenter__(self):
                    return client

                async def __aexit__(self, *_exc):
                    return None

            return _Mgr()

    _register_reconcile_bridge_hooks(fake_client_cls=_FakeClient)
    return client


def _insert_running(
    db_path: Path,
    *,
    run_id: str,
    agent_id: str,
    updated_at: str,
    event_at: str | None,
) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO agents VALUES (?,?,?,?,?,?,?,?,?)",
            (
                agent_id,
                "/ws",
                "IDLE",
                run_id,
                None,
                None,
                "{}",
                updated_at,
                updated_at,
            ),
        )
        db.execute(
            "INSERT INTO runs (run_id, agent_id, turn_number, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (run_id, agent_id, 1, "RUNNING", updated_at, updated_at),
        )
        if event_at is not None:
            db.execute(
                "INSERT INTO run_events (run_id, seq, offset, event_type, created_at) "
                "VALUES (?,?,?,?,?)",
                (run_id, 1, "0", "ping", event_at),
            )


def test_resolve_verify_impl_store_paths(tmp_path: Path) -> None:
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("visual", repo_root=tmp_path)
    assert ctx.store_backend == "sqlite"
    assert ctx.store_scope == "verify_impl:visual"
    assert ctx.index_db == ctx.sqlite_root / "index.db"
    kwargs = ctx.bridge_launch_kwargs()
    assert kwargs["local"]["store"]["type"] == "sqlite"
    assert kwargs["state_root"] == str(ctx.sqlite_root)


def test_list_running_runs_orphan_stale(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    _ensure_index_schema(db_path)
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    _insert_running(db_path, run_id="run-old", agent_id="agent-1", updated_at=old, event_at=None)
    rows = list_running_runs(db_path)
    assert len(rows) == 1
    assert rows[0].run_id == "run-old"


def test_list_running_ignores_finished(tmp_path: Path) -> None:
    db_path = tmp_path / "index.db"
    _ensure_index_schema(db_path)
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO runs (run_id, agent_id, turn_number, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            ("run-done", "agent-x", 1, "FINISHED", now, now),
        )
    assert list_running_runs(db_path) == []


def test_decide_pause_on_live_running_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("alpha", repo_root=tmp_path)
    ctx.sqlite_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    _ensure_index_schema(ctx.index_db)
    _insert_running(
        ctx.index_db,
        run_id="run-live",
        agent_id="agent-live",
        updated_at=now,
        event_at=now,
    )
    _patch_get_run_bridge(monkeypatch, status="RUNNING")
    decision = decide_mutating_dispatch_allowed(
        change_id="alpha",
        stage="APPLY_RED",
        repo_root=tmp_path,
    )
    assert isinstance(decision, Pause)
    assert "run-live" in decision.reason
    assert "sdk_index_get_run_block" in decision.reason


def test_decide_empty_reconcile_allows_clean_ledger_and_index(tmp_path: Path) -> None:
    """Пустой reconcile (как в graph gate) не блокирует без ledger/index конфликта."""
    (tmp_path / "opsx").mkdir()
    decision = decide_mutating_dispatch_allowed(
        change_id="clean",
        stage="APPLY_RED",
        repo_root=tmp_path,
        reconcile=AdapterRunReconcileReport(change_id="clean", index_db=tmp_path, rows=[]),
    )
    assert isinstance(decision, Allow)


def test_decide_empty_reconcile_pauses_on_ledger_active(tmp_path: Path) -> None:
    (tmp_path / "opsx").mkdir()
    ledger_path = dispatch_ledger_path("ledger-only", repo_root=tmp_path)
    ledger = ImplementationDispatchLedger(ledger_path)
    ledger.upsert_active_hop(
        change_id="ledger-only",
        slice_id="SLICE-1",
        stage="APPLY_RED",
        agent_id="agent-1",
        run_id="run-active",
        sdk_status="RUNNING",
    )
    decision = decide_mutating_dispatch_allowed(
        change_id="ledger-only",
        stage="APPLY_RED",
        repo_root=tmp_path,
        reconcile=AdapterRunReconcileReport(change_id="ledger-only", index_db=tmp_path, rows=[]),
        ledger=ledger,
    )
    assert isinstance(decision, Pause)
    assert "run-active" in decision.reason


def test_empty_reconcile_ephemeral_bridge_verify_impl_store_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty-reconcile GetRun must launch_bridge with verify_impl sqlite identity."""
    (tmp_path / "opsx").mkdir()
    change_id = "visual"
    ctx = resolve_verify_impl_adapter_store(change_id, repo_root=tmp_path)
    ctx.sqlite_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    _ensure_index_schema(ctx.index_db)
    _insert_running(
        ctx.index_db,
        run_id="run-store-kwargs",
        agent_id="agent-v",
        updated_at=now,
        event_at=now,
    )
    expected_launch = ctx.bridge_launch_kwargs()
    captured: list[dict] = []

    auth = MagicMock()
    auth.api_key = "test-key"
    monkeypatch.setattr(
        "workflows.implementation.adapter_run_reconcile.resolve_auth",
        lambda: auth,
    )
    client = AsyncMock()
    run_obj = MagicMock()
    run_obj.status = "RUNNING"
    client.get_run = AsyncMock(return_value=run_obj)

    class _FakeClient:
        @staticmethod
        async def launch_bridge(**kwargs):
            captured.append(kwargs)

            class _Mgr:
                async def __aenter__(self):
                    return client

                async def __aexit__(self, *_exc):
                    return None

            return _Mgr()

    _register_reconcile_bridge_hooks(fake_client_cls=_FakeClient)

    _ = decide_mutating_dispatch_allowed(
        change_id=change_id,
        stage="APPLY_RED",
        repo_root=tmp_path,
        reconcile=AdapterRunReconcileReport(
            change_id=change_id, index_db=ctx.index_db, rows=[],
        ),
    )

    assert len(captured) == 1
    assert captured[0] == expected_launch
    assert captured[0]["local"]["store"]["type"] == "sqlite"
    assert captured[0]["state_root"] == str(ctx.sqlite_root)


def test_decide_empty_reconcile_pauses_on_index_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пустые rows: RUNNING index блокируется только после bounded GetRun."""
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("epsilon", repo_root=tmp_path)
    ctx.sqlite_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    _ensure_index_schema(ctx.index_db)
    _insert_running(
        ctx.index_db,
        run_id="run-empty-rows",
        agent_id="agent-e",
        updated_at=now,
        event_at=now,
    )
    _patch_get_run_bridge(monkeypatch, status="RUNNING")
    decision = decide_mutating_dispatch_allowed(
        change_id="epsilon",
        stage="APPLY_RED",
        repo_root=tmp_path,
        reconcile=AdapterRunReconcileReport(change_id="epsilon", index_db=tmp_path, rows=[]),
    )
    assert isinstance(decision, Pause)
    assert "run-empty-rows" in decision.reason
    assert "sdk_index_get_run_block" in decision.reason


def test_decide_empty_reconcile_allows_index_terminal_via_get_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite RUNNING + GetRun terminal — не блокируем новый mutating writer."""
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("zeta", repo_root=tmp_path)
    ctx.sqlite_root.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    _ensure_index_schema(ctx.index_db)
    _insert_running(
        ctx.index_db,
        run_id="run-stale-sqlite",
        agent_id="agent-z",
        updated_at=old,
        event_at=None,
    )
    _patch_get_run_bridge(monkeypatch, status="finished")
    decision = decide_mutating_dispatch_allowed(
        change_id="zeta",
        stage="APPLY_RED",
        repo_root=tmp_path,
        reconcile=AdapterRunReconcileReport(change_id="zeta", index_db=tmp_path, rows=[]),
    )
    assert isinstance(decision, Allow)


def test_decide_empty_reconcile_pause_when_get_run_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Нет API key / bridge — fail-closed Pause, без второго writer."""
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("eta", repo_root=tmp_path)
    ctx.sqlite_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    _ensure_index_schema(ctx.index_db)
    _insert_running(
        ctx.index_db,
        run_id="run-no-key",
        agent_id="agent-eta",
        updated_at=now,
        event_at=now,
    )
    auth = MagicMock()
    auth.api_key = ""
    monkeypatch.setattr(
        "workflows.implementation.adapter_run_reconcile.resolve_auth",
        lambda: auth,
    )
    decision = decide_mutating_dispatch_allowed(
        change_id="eta",
        stage="APPLY_RED",
        repo_root=tmp_path,
        reconcile=AdapterRunReconcileReport(change_id="eta", index_db=tmp_path, rows=[]),
    )
    assert isinstance(decision, Pause)
    assert "sdk_index_get_run_unavailable" in decision.reason
    assert "run-no-key" in decision.reason


def test_decide_allow_when_orphan_stale_in_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SQLite RUNNING + GetRun RUNNING, но stale local — orphan, не live SDK hop."""
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("beta", repo_root=tmp_path)
    ctx.sqlite_root.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    _ensure_index_schema(ctx.index_db)
    _insert_running(
        ctx.index_db,
        run_id="run-orphan",
        agent_id="agent-o",
        updated_at=old,
        event_at=None,
    )
    _patch_get_run_bridge(monkeypatch, status="RUNNING")
    decision = decide_mutating_dispatch_allowed(
        change_id="beta",
        stage="APPLY_RED",
        repo_root=tmp_path,
    )
    assert isinstance(decision, Allow)


def test_decide_allow_when_ledger_paused_matches_orphan_reconcile(tmp_path: Path) -> None:
    """Ledger PAUSED + parent ORPHAN reconcile снимает dispatch gate без GetRun."""
    (tmp_path / "opsx").mkdir()
    ledger_path = dispatch_ledger_path("visual", repo_root=tmp_path)
    ledger = ImplementationDispatchLedger(ledger_path)
    run_id = "run-2b95286a-ad79-4992-b3cb-538fcd6b3ea6"
    ledger.upsert_active_hop(
        change_id="visual",
        slice_id="SLICE-004",
        stage="APPLY_GREEN",
        agent_id="agent-g",
        run_id=run_id,
        sdk_status="PAUSED",
    )
    report = AdapterRunReconcileReport(change_id="visual", index_db=tmp_path)
    report.rows.append(
        AdapterRunReconcileRow(
            record=RunningRunRecord(
                run_id=run_id,
                agent_id="agent-g",
                status="RUNNING",
                updated_at="2020-01-01T00:00:00+00:00",
                last_event_at=None,
            ),
            disposition=AdapterRunDisposition.ORPHAN_CANDIDATE,
            get_run_status="running",
            detail="stale_updated_at_no_recent_events",
        ),
    )
    decision = decide_mutating_dispatch_allowed(
        change_id="visual",
        stage="APPLY_GREEN",
        repo_root=tmp_path,
        reconcile=report,
        ledger=ledger,
        bridge=None,
    )
    assert isinstance(decision, Allow)
    assert ledger.get_conflicting_active("visual", "APPLY_GREEN") == []


def test_resolve_dispatch_exclude_run_id_requires_paused_same_stage() -> None:
    assert (
        resolve_dispatch_exclude_run_id(
            stage="APPLY_IMPLEMENT",
            run_id="run-a",
            implementation_stage="APPLY_IMPLEMENT",
            implementation_status="PAUSED",
            implementation_recovery_code="SDK_ACTIVE_RUN_CONFLICT",
        )
        == "run-a"
    )
    assert (
        resolve_dispatch_exclude_run_id(
            stage="APPLY_IMPLEMENT",
            run_id="run-a",
            implementation_stage="APPLY_RED",
            implementation_status="PAUSED",
            implementation_recovery_code="SDK_ACTIVE_RUN_CONFLICT",
        )
        is None
    )


def test_resolve_dispatch_exclude_run_id_running_same_stage() -> None:
    """После NO_CHANGE lane RUNNING: свой run_id не блокирует следующий dispatch."""
    assert (
        resolve_dispatch_exclude_run_id(
            stage="APPLY_SCAFFOLD",
            run_id="run-just-finished",
            implementation_stage="APPLY_SCAFFOLD",
            implementation_status="RUNNING",
            implementation_recovery_code=None,
        )
        == "run-just-finished"
    )


def test_decide_allow_when_ledger_terminal_but_session_reconcile_running_live(
    tmp_path: Path,
) -> None:
    """Stale session reconcile RUNNING_LIVE не блокирует, если ledger уже FINISHED."""
    (tmp_path / "opsx").mkdir()
    ledger_path = dispatch_ledger_path("visual", repo_root=tmp_path)
    ledger = ImplementationDispatchLedger(ledger_path)
    run_id = "run-scaffold-done"
    ledger.upsert_active_hop(
        change_id="visual",
        slice_id="SLICE-001",
        stage="APPLY_SCAFFOLD",
        agent_id="agent-s",
        run_id=run_id,
        sdk_status="FINISHED",
    )
    now = datetime.now(UTC).isoformat()
    report = AdapterRunReconcileReport(change_id="visual", index_db=tmp_path)
    report.rows.append(
        AdapterRunReconcileRow(
            record=RunningRunRecord(
                run_id=run_id,
                agent_id="agent-s",
                status="RUNNING",
                updated_at=now,
                last_event_at=now,
            ),
            disposition=AdapterRunDisposition.RUNNING_LIVE,
            get_run_status="RUNNING",
            detail="recent_run_events",
        ),
    )
    decision = decide_mutating_dispatch_allowed(
        change_id="visual",
        stage="APPLY_SCAFFOLD",
        repo_root=tmp_path,
        reconcile=report,
        ledger=ledger,
    )
    assert isinstance(decision, Allow)


def test_decide_allow_when_session_reconcile_stale_but_get_run_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session reconcile RUNNING_LIVE не блокирует, если GetRun на gate уже terminal."""
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("visual", repo_root=tmp_path)
    now = datetime.now(UTC).isoformat()
    report = AdapterRunReconcileReport(change_id="visual", index_db=ctx.index_db)
    report.rows.append(
        AdapterRunReconcileRow(
            record=RunningRunRecord(
                run_id="run-stale-session",
                agent_id="agent-s",
                status="RUNNING",
                updated_at=now,
                last_event_at=now,
            ),
            disposition=AdapterRunDisposition.RUNNING_LIVE,
            get_run_status="RUNNING",
            detail="recent_run_events",
        ),
    )
    _patch_get_run_bridge(monkeypatch, status="finished")
    decision = decide_mutating_dispatch_allowed(
        change_id="visual",
        stage="APPLY_IMPLEMENT",
        repo_root=tmp_path,
        reconcile=report,
    )
    assert isinstance(decision, Allow)


def test_decide_allow_when_live_run_is_other_stage_in_ledger(tmp_path: Path) -> None:
    """RUNNING_LIVE для другого stage в ledger не блокирует APPLY_IMPLEMENT."""
    (tmp_path / "opsx").mkdir()
    ledger_path = dispatch_ledger_path("visual", repo_root=tmp_path)
    ledger = ImplementationDispatchLedger(ledger_path)
    run_id = "run-red-live"
    ledger.upsert_active_hop(
        change_id="visual",
        slice_id="SLICE-004",
        stage="APPLY_RED",
        agent_id="agent-r",
        run_id=run_id,
        sdk_status="RUNNING",
    )
    report = AdapterRunReconcileReport(change_id="visual", index_db=tmp_path)
    report.rows.append(
        AdapterRunReconcileRow(
            record=RunningRunRecord(
                run_id=run_id,
                agent_id="agent-r",
                status="RUNNING",
                updated_at=datetime.now(UTC).isoformat(),
                last_event_at=datetime.now(UTC).isoformat(),
            ),
            disposition=AdapterRunDisposition.RUNNING_LIVE,
            get_run_status="RUNNING",
            detail="recent_run_events",
        ),
    )
    auth_patch = pytest.MonkeyPatch()
    auth = MagicMock()
    auth.api_key = ""
    auth_patch.setattr(
        "workflows.implementation.adapter_run_reconcile.resolve_auth",
        lambda: auth,
    )
    try:
        decision = decide_mutating_dispatch_allowed(
            change_id="visual",
            stage="APPLY_IMPLEMENT",
            repo_root=tmp_path,
            reconcile=report,
            ledger=ledger,
        )
    finally:
        auth_patch.undo()
    assert isinstance(decision, Allow)


def test_decide_allow_when_reconcile_report_has_orphan_rows(tmp_path: Path) -> None:
    """Parent ``reconcile_verify_impl_adapter_runs`` с ORPHAN не блокирует mutating writer."""
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("visual", repo_root=tmp_path)
    report = AdapterRunReconcileReport(change_id="visual", index_db=ctx.index_db)
    report.rows.append(
        AdapterRunReconcileRow(
            record=RunningRunRecord(
                run_id="run-orphan",
                agent_id="agent-o",
                status="RUNNING",
                updated_at="2020-01-01T00:00:00+00:00",
                last_event_at=None,
            ),
            disposition=AdapterRunDisposition.ORPHAN_CANDIDATE,
            get_run_status="RUNNING",
            detail="stale_updated_at_no_recent_events",
        ),
    )
    decision = decide_mutating_dispatch_allowed(
        change_id="visual",
        stage="APPLY_IMPLEMENT",
        repo_root=tmp_path,
        reconcile=report,
    )
    assert isinstance(decision, Allow)


def test_dispatch_ledger_path_and_conflict(tmp_path: Path) -> None:
    (tmp_path / "opsx").mkdir()
    path = dispatch_ledger_path("visual", repo_root=tmp_path)
    assert path.name == "dispatch.sqlite"
    assert "impl-visual" in str(path)
    ledger = ImplementationDispatchLedger(path)
    ledger.upsert_active_hop(
        change_id="visual",
        slice_id="SLICE-001",
        stage="APPLY_RED",
        agent_id="agent-1",
        run_id="run-1",
        sdk_status="RUNNING",
    )
    conflicts = ledger.get_conflicting_active("visual", "APPLY_RED")
    assert len(conflicts) == 1
    assert conflicts[0].run_id == "run-1"
    ledger.clear_terminal(change_id="visual", run_id="run-1", sdk_status="FINISHED")
    assert ledger.get_conflicting_active("visual", "APPLY_RED") == []
    row = ledger.load_row("visual", "run-1")
    assert row is not None
    assert row.sdk_status == "FINISHED"


def test_dispatch_ledger_survives_outside_checkpoint_dir(tmp_path: Path) -> None:
    """Ledger лежит рядом с impl-<id>, не внутри checkpoints.sqlite."""
    (tmp_path / "opsx").mkdir()
    ckpt_dir = tmp_path / "opsx" / "runs" / "verify" / "impl-visual"
    ckpt_dir.mkdir(parents=True)
    ckpt = ckpt_dir / "checkpoints.sqlite"
    ckpt.write_text("langgraph", encoding="utf-8")
    ledger_path = dispatch_ledger_path("visual", repo_root=tmp_path)
    ledger = ImplementationDispatchLedger(ledger_path)
    ledger.upsert_active_hop(
        change_id="visual",
        slice_id=None,
        stage="APPLY_DESIGN",
        agent_id="a",
        run_id="r",
        sdk_status="RUNNING",
    )
    assert ledger_path.is_file()
    assert ledger_path.parent == ckpt_dir


@pytest.mark.asyncio
async def test_reconcile_classifies_finished_via_get_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("gamma", repo_root=tmp_path)
    ctx.sqlite_root.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    _ensure_index_schema(ctx.index_db)
    _insert_running(
        ctx.index_db,
        run_id="run-fin",
        agent_id="agent-f",
        updated_at=old,
        event_at=None,
    )

    auth = MagicMock()
    auth.api_key = "test-key"
    monkeypatch.setattr(
        "workflows.implementation.adapter_run_reconcile.resolve_auth",
        lambda: auth,
    )

    client = AsyncMock()
    run_obj = MagicMock()
    run_obj.status = "finished"
    client.get_run = AsyncMock(return_value=run_obj)

    launched = False

    class _FakeClient:
        @staticmethod
        async def launch_bridge(**_kwargs):
            nonlocal launched
            launched = True

            class _Mgr:
                async def __aenter__(self):
                    return client

                async def __aexit__(self, *_exc):
                    return None

            return _Mgr()

    _register_reconcile_bridge_hooks(fake_client_cls=_FakeClient)

    report = await reconcile_verify_impl_adapter_runs("gamma", tmp_path, None)
    assert launched
    assert len(report.rows) == 1
    assert report.rows[0].disposition == AdapterRunDisposition.FINISHED


@pytest.mark.asyncio
async def test_reconcile_orphan_candidate_when_running_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("delta", repo_root=tmp_path)
    ctx.sqlite_root.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    _ensure_index_schema(ctx.index_db)
    _insert_running(
        ctx.index_db,
        run_id="run-stale",
        agent_id="agent-s",
        updated_at=old,
        event_at=None,
    )

    auth = MagicMock()
    auth.api_key = "test-key"
    monkeypatch.setattr(
        "workflows.implementation.adapter_run_reconcile.resolve_auth",
        lambda: auth,
    )

    client = AsyncMock()
    run_obj = MagicMock()
    run_obj.status = "RUNNING"
    client.get_run = AsyncMock(return_value=run_obj)

    class _FakeClient:
        @staticmethod
        async def launch_bridge(**_kwargs):
            class _Mgr:
                async def __aenter__(self):
                    return client

                async def __aexit__(self, *_exc):
                    return None

            return _Mgr()

    _register_reconcile_bridge_hooks(fake_client_cls=_FakeClient)

    report = await reconcile_verify_impl_adapter_runs("delta", tmp_path, None)
    assert report.rows[0].disposition == AdapterRunDisposition.ORPHAN_CANDIDATE


@pytest.mark.asyncio
async def test_decide_mutating_from_async_context_ignores_live_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GetRun gate из async hop не должен звать bridge с закрытым root event loop."""
    (tmp_path / "opsx").mkdir()
    ctx = resolve_verify_impl_adapter_store("loop-fix", repo_root=tmp_path)
    ctx.sqlite_root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    _ensure_index_schema(ctx.index_db)
    _insert_running(
        ctx.index_db,
        run_id="run-loop-bridge",
        agent_id="agent-lb",
        updated_at=now,
        event_at=now,
    )
    bridge = MagicMock()
    bridge._started = True
    bridge.connection = MagicMock(
        side_effect=AssertionError("bridge.connection must not be used from sync gate"),
    )

    auth = MagicMock()
    auth.api_key = "test-key"
    monkeypatch.setattr(
        "workflows.implementation.adapter_run_reconcile.resolve_auth",
        lambda: auth,
    )
    client = AsyncMock()
    client.get_run = AsyncMock(return_value=MagicMock(status="finished"))
    launched: list[dict] = []

    class _FakeClient:
        @staticmethod
        async def launch_bridge(**kwargs):
            launched.append(kwargs)

            class _Mgr:
                async def __aenter__(self):
                    return client

                async def __aexit__(self, *_exc):
                    return None

            return _Mgr()

    _register_reconcile_bridge_hooks(fake_client_cls=_FakeClient)

    async def _invoke_gate() -> object:
        return decide_mutating_dispatch_allowed(
            change_id="loop-fix",
            stage="APPLY_IMPLEMENT",
            repo_root=tmp_path,
            reconcile=AdapterRunReconcileReport(
                change_id="loop-fix",
                index_db=ctx.index_db,
                rows=[],
            ),
            bridge=bridge,
        )

    decision = await _invoke_gate()
    assert isinstance(decision, Allow)
    assert launched, "expected ephemeral AsyncClient.launch_bridge in worker-thread gate"
    bridge.connection.assert_not_called()


def test_run_async_gate_coro_uses_fresh_loop_when_current_closed() -> None:
    """Sync gate path: new loop even if current event loop is already closed."""

    async def _probe() -> str:
        return asyncio.get_running_loop().is_closed()

    stale = asyncio.new_event_loop()
    stale.close()
    asyncio.set_event_loop(stale)

    assert _run_async_gate_coro(_probe()) is False
    asyncio.set_event_loop(None)
