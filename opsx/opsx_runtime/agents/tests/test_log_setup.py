"""Тесты OPSX stderr logging: relpath-формат и уровни DEBUG vs ERROR."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.log_setup import OpsxStderrFormatter, _default_repo_root, create_opsx_stderr_formatter


def test_opsx_stderr_formatter_uses_repo_relative_path() -> None:
    repo = _default_repo_root()
    formatter = create_opsx_stderr_formatter(repo_root=repo)
    record = logging.LogRecord(
        name="workflows.checkpoint_resume",
        level=logging.DEBUG,
        pathname=str(
            repo / "opsx" / "opsx_runtime" / "workflows" / "checkpoint_resume.py",
        ),
        lineno=1379,
        msg="checkpoint exit=return_terminal_snapshot thread_id=t-1",
        args=(),
        exc_info=None,
    )
    line = formatter.format(record)
    assert "opsx/opsx_runtime/workflows/checkpoint_resume.py:1379" in line
    assert "DEBUG" in line
    assert "workflows.checkpoint_resume" in line
    assert "return_terminal_snapshot" in line


def test_opsx_formatter_fallback_outside_repo() -> None:
    formatter = OpsxStderrFormatter(repo_root=_default_repo_root())
    outside = "/tmp/outside_opsx/foo.py"
    record = logging.LogRecord(
        name="x",
        level=logging.DEBUG,
        pathname=outside,
        lineno=1,
        msg="m",
        args=(),
        exc_info=None,
    )
    line = formatter.format(record)
    assert outside in line


def test_live_stream_progress_is_debug_fault_is_error() -> None:
    from agents.adapters.agent_stream import live_stream_log_level

    assert live_stream_log_level({"kind": "thinking", "text": "x"}) == logging.DEBUG
    assert live_stream_log_level({"kind": "stream_unknown"}) == logging.DEBUG
    assert (
        live_stream_log_level({"kind": "terminal", "status": "error"})
        == logging.ERROR
    )


@pytest.mark.asyncio
async def test_invoke_checkpointed_graph_terminal_exit_logs_debug(caplog) -> None:
    """Terminal thread без pending → DEBUG exit=return_terminal_snapshot."""
    from workflows.checkpoint_resume import invoke_checkpointed_graph

    thread_id = "verify-impl:sample-change"
    root_snapshot = MagicMock()
    root_snapshot.values = {"verdict": "IMPL_OK", "change_id": "sample-change"}
    root_snapshot.next = ()
    root_snapshot.tasks = ()

    graph = MagicMock()
    graph.checkpointer = MagicMock()
    graph.nodes = {}
    graph.aupdate_state = AsyncMock()
    graph.aget_state = AsyncMock(return_value=root_snapshot)

    with (
        patch(
            "workflows.checkpoint_resume.plan_static_repair_rewind",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume._resolve_nested_pending_subgraph",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume._find_crash_bookmark_from_history",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume.plan_stale_end_resume",
            AsyncMock(return_value=None),
        ),
        patch(
            "workflows.checkpoint_resume.resolve_opsx_repository_root",
            lambda path: path,
        ),
        patch(
            "workflows.checkpoint_resume.graph_repo_path_patch",
            lambda _state, _root: {},
        ),
        caplog.at_level(logging.DEBUG, logger="workflows.checkpoint_resume"),
    ):
        result = await invoke_checkpointed_graph(
            graph,
            initial_state={"change_id": "sample-change", "repository_path": "/tmp/repo"},
            thread_id=thread_id,
            repo_root=Path("/tmp/repo"),
        )

    assert result["verdict"] == "IMPL_OK"
    exit_records = [
        r
        for r in caplog.records
        if "checkpoint exit=return_terminal_snapshot" in r.message
    ]
    assert exit_records
    assert exit_records[0].levelname == "DEBUG"
