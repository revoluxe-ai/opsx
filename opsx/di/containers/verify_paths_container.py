"""Контейнер путей verify-сессии и начального ``GraphState`` (dependency-injector)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from dependency_injector import containers, providers

from opsx.di._runtime_imports import ensure_opsx_runtime_importable
from opsx.di.types import VerifySessionPaths

if TYPE_CHECKING:
    from workflows.checkpoint_resume import WorkflowResumeScope


def _prepare_session_paths(
    repo_root: Path,
    change_id: str,
    session_id: str | None = None,
) -> VerifySessionPaths:
    """Создать verify_dir и пути live-логов для одной сессии."""
    ensure_opsx_runtime_importable(repo_root=repo_root)
    from agents.verify.impl_verify.preflight import change_root

    cid = change_id.strip()
    if not cid:
        raise ValueError("VERIFY_IMPL_CHANGE_ID_REQUIRED")
    sid = session_id or uuid.uuid4().hex[:12]
    verify_dir = change_root(cid, repo_root=repo_root) / "verify"
    verify_dir.mkdir(parents=True, exist_ok=True)
    return VerifySessionPaths(
        session_id=sid,
        verify_dir=verify_dir,
        stream_log=verify_dir / f"impl-verify-{sid}.live.ndjson",
        group_error_stream_log=verify_dir / f"group-error-{sid}.live.ndjson",
        repair_order_stream_log=verify_dir / f"repair-order-{sid}.live.ndjson",
        diagnostic_stream_log=verify_dir / f"diagnostic-{sid}.live.ndjson",
    )


def _build_initial_state(
    repo_root: Path,
    change_id: str,
    session_paths: VerifySessionPaths,
) -> dict[str, object]:
    """Собрать ``initial_state`` parent graph (без вызова workflow)."""
    ensure_opsx_runtime_importable(repo_root=repo_root)
    from workflows.repair.cursor_adapter import default_hop_model_selection
    from workflows.repo_paths import graph_repo_path_patch

    cid = change_id.strip()
    dry_run = os.environ.get("OPSX_IMPLEMENTATION_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    state: dict[str, object] = {
        **graph_repo_path_patch({}, repo_root),
        "change_id": cid,
        "run_uuid": session_paths.session_id,
        "live_execution_authorized": not dry_run,
        "model": default_hop_model_selection(),
        "stream_log": str(session_paths.stream_log),
        "stream_label": f"verify-impl-{cid}",
        "group_error_stream_log": str(session_paths.group_error_stream_log),
        "group_error_stream_label": f"group-error-{cid}",
        "repair_order_stream_log": str(session_paths.repair_order_stream_log),
        "repair_order_stream_label": f"repair-order-{cid}",
        "diagnostic_stream_log": str(session_paths.diagnostic_stream_log),
        "diagnostic_stream_label": f"diagnostic-{cid}",
    }
    return state


def _build_resume_scope(
    repo_root: Path,
    change_id: str,
) -> WorkflowResumeScope | None:
    """``WorkflowResumeScope`` при включённом durable LangGraph checkpoint."""
    ensure_opsx_runtime_importable(repo_root=repo_root)
    from workflows.checkpoint_resume import (
        WorkflowResumeScope,
        durable_langgraph_checkpoint_enabled,
    )

    cid = change_id.strip()
    if durable_langgraph_checkpoint_enabled():
        return WorkflowResumeScope(kind="verify_impl", change_id=cid)
    return None


class VerifyPathsContainer(containers.DeclarativeContainer):
    """Каталог ``openspec/.../verify``, ndjson stream logs и patch репозитория."""

    repo_root = providers.Object(Path())
    change_id = providers.Object("")

    session_paths = providers.Factory(
        _prepare_session_paths,
        repo_root=repo_root,
        change_id=change_id,
        session_id=None,
    )
    initial_state = providers.Factory(
        _build_initial_state,
        repo_root=repo_root,
        change_id=change_id,
        session_paths=providers.Dependency(),
    )
    resume_scope = providers.Callable(
        _build_resume_scope,
        repo_root=repo_root,
        change_id=change_id,
    )
