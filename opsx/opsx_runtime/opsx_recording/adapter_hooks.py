"""Точки подключения записи inbox к адаптерам провайдера.

rebase synthesis: inbox capture lifecycle from incoming d749a07e + terminal receipt
and artifact_paths from corrective dev (fail-closed finalize, not abort-only).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Mapping

from agents.adapters.abc_adapter import AgentResult
from opsx_recording.inbox_capture import (
    OpsxCaptureSession,
    abort_opsx_sdk_capture,
    begin_opsx_sdk_capture,
    complete_opsx_sdk_capture,
)
from run_dialogs import infer_agent_folder_name


def begin_adapter_inbox_capture(
    state: Mapping[str, Any],
    *,
    stream_label: str,
    stream_log: str | Path | None,
    resume_agent_id: str | None = None,
    resume_run_id: str | None = None,
) -> OpsxCaptureSession | None:
    """Резерв inbox до adapter hop."""
    agent_slug = infer_agent_folder_name(
        state=state,
        stream_label=stream_label,
        stream_log=stream_log,
    )
    return begin_opsx_sdk_capture(
        state,
        agent_slug=agent_slug,
        stream_label=stream_label,
        stream_log=stream_log,
        resume_agent_id=resume_agent_id,
        resume_run_id=resume_run_id,
    )


def finish_adapter_inbox_capture(
    session: OpsxCaptureSession | None,
    *,
    state: Mapping[str, Any],
    stream_log: str | Path | None,
    agent_result: AgentResult | None,
    run_dialog_agent_dir: Path | None,
    run_dialog_artifact_paths: Mapping[str, Path] | None = None,
    error: str | None = None,
    terminal_code: str | None = None,
) -> Path | None:
    return complete_opsx_sdk_capture(
        session,
        state=state,
        stream_log=stream_log,
        agent_result=agent_result,
        run_dialog_agent_dir=run_dialog_agent_dir,
        run_dialog_artifact_paths=run_dialog_artifact_paths,
        error=error,
        terminal_code=terminal_code,
    )


def abort_adapter_inbox_capture(
    session: OpsxCaptureSession | None,
    *,
    error: str,
) -> None:
    abort_opsx_sdk_capture(session, error=error)


def _terminal_error_from_exc(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, asyncio.CancelledError):
        return f"CancelledError: {exc}", "CANCELLED"
    return f"{type(exc).__name__}: {exc}", "FAILED"


@asynccontextmanager
async def adapter_inbox_capture_scope(
    state: Mapping[str, Any],
    *,
    stream_label: str,
    stream_log: str | Path | None,
    resume_agent_id: str | None = None,
) -> AsyncIterator[OpsxCaptureSession | None]:
    """Резерв inbox до hop и terminal receipt при необработанном прерывании."""
    session = begin_adapter_inbox_capture(
        state,
        stream_label=stream_label,
        stream_log=stream_log,
        resume_agent_id=resume_agent_id,
    )
    try:
        yield session
    except asyncio.CancelledError as exc:
        message, code = _terminal_error_from_exc(exc)
        _ = finish_adapter_inbox_capture(
            session,
            state=state,
            stream_log=stream_log,
            agent_result=None,
            run_dialog_agent_dir=None,
            error=message,
            terminal_code=code,
        )
        raise
    except Exception as exc:
        message, code = _terminal_error_from_exc(exc)
        _ = finish_adapter_inbox_capture(
            session,
            state=state,
            stream_log=stream_log,
            agent_result=None,
            run_dialog_agent_dir=None,
            error=message,
            terminal_code=code,
        )
        raise
