"""Политика durable checkpoint для parent graph entry (``workflows.main_graph``)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from agents.state import GraphState
from workflows.checkpoint_resume import (
    WorkflowResumeScope,
    durable_langgraph_checkpoint_enabled,
    invoke_ephemeral_workflow_graph,
)
from workflows.main_graph import _run_compiled_main_graph
from workflows.repo_paths import resolve_opsx_repository_root

_REPO_ROOT = resolve_opsx_repository_root(Path(__file__).resolve().parents[4])


def test_verify_impl_durable_checkpoint_disabled_by_default() -> None:
    """Verify-impl PP не пишет checkpoints.sqlite без явного включения флага."""
    assert durable_langgraph_checkpoint_enabled() is False


@pytest.mark.asyncio
async def test_main_graph_run_ephemeral_skips_sqlite_and_resume_scope(tmp_path: Path) -> None:
    """``_run_compiled_main_graph`` при отключённом checkpoint: checkpointer=None, без SQLite."""
    checkpointers: list[object | None] = []

    def factory(checkpointer: object | None) -> MagicMock:
        from types import SimpleNamespace

        checkpointers.append(checkpointer)
        graph = MagicMock()
        graph.checkpointer = checkpointer
        graph.ainvoke = AsyncMock(return_value={"verdict": "STUB_OK"})
        graph.aget_state = AsyncMock(
            return_value=SimpleNamespace(
                next=("noop",),
                values={"verdict": "STUB_OK", "quality_gates_execution_status": "OK"},
            ),
        )
        return graph

    resume_scope = WorkflowResumeScope(kind="verify_impl", change_id="policy-test")
    with patch("workflows.main_graph.AsyncSqliteSaver") as sqlite_mock:
        result = await _run_compiled_main_graph(
            factory,
            initial_state={
                "change_id": "policy-test",
                "repository_path": str(_REPO_ROOT),
                "run_uuid": "sess-1",
            },
            repo_root=_REPO_ROOT,
            thread_id="",
            resume_scope=resume_scope,
            run_dir=None,
            checkpoint_path=None,
        )

    sqlite_mock.from_conn_string.assert_not_called()
    assert len(checkpointers) == 1
    assert isinstance(checkpointers[0], MemorySaver)
    assert result.get("verdict") == "STUB_OK"


@pytest.mark.asyncio
async def test_invoke_ephemeral_workflow_graph_omits_durability_kwarg() -> None:
    """Ephemeral ainvoke не передаёт durability=sync (иначе LangGraph падает без checkpointer)."""
    observed_kwargs: dict[str, object] = {}

    async def _noop(_state: GraphState) -> dict[str, str]:
        return {"verdict": "EPHEMERAL_OK"}

    builder = StateGraph(GraphState)
    builder.add_node("noop", _noop)
    _ = builder.add_edge(START, "noop")
    _ = builder.add_edge("noop", END)
    graph = builder.compile(checkpointer=None)

    real_ainvoke = graph.ainvoke

    async def _recording_ainvoke(*args: object, **kwargs: object) -> object:
        observed_kwargs.update(kwargs)
        return await real_ainvoke(*args, **kwargs)

    graph.ainvoke = _recording_ainvoke  # type: ignore[method-assign]

    result = await invoke_ephemeral_workflow_graph(
        graph,
        initial_state={
            "change_id": "policy-test",
            "repository_path": str(_REPO_ROOT),
            "run_uuid": "ephemeral-1",
        },
        repo_root=_REPO_ROOT,
    )

    assert "durability" not in observed_kwargs
    assert result.get("verdict") == "EPHEMERAL_OK"


@pytest.mark.asyncio
async def test_invoke_ephemeral_skips_maybe_continue_pipeline() -> None:
    """Ephemeral path: один ``ainvoke``, без ``_maybe_continue_incomplete_verify_pipeline``."""
    graph = MagicMock()
    graph.checkpointer = MemorySaver()
    graph.ainvoke = AsyncMock(return_value={"verdict": "EPHEMERAL_OK"})
    with patch(
        "workflows.checkpoint_resume._maybe_continue_incomplete_verify_pipeline",
        AsyncMock(side_effect=AssertionError("ephemeral must not continue")),
    ):
        result = await invoke_ephemeral_workflow_graph(
            graph,
            initial_state={
                "change_id": "policy-test",
                "repository_path": str(_REPO_ROOT),
                "run_uuid": "ephemeral-no-continue",
            },
            repo_root=_REPO_ROOT,
        )
    graph.ainvoke.assert_awaited_once()
    assert result.get("verdict") == "EPHEMERAL_OK"


@pytest.mark.asyncio
async def test_maybe_continue_skipped_without_in_memory_saver() -> None:
    """Без checkpointer (ни SQLite, ни MemorySaver) stale-end continuation не запускается."""
    graph = MagicMock()
    graph.checkpointer = None
    graph.aget_state = AsyncMock()
    with patch(
        "workflows.checkpoint_resume.plan_stale_end_resume",
        AsyncMock(return_value=object()),
    ) as plan_mock:
        merged = await __import__(
            "workflows.checkpoint_resume",
            fromlist=["_maybe_continue_incomplete_verify_pipeline"],
        )._maybe_continue_incomplete_verify_pipeline(
            graph,
            config={"configurable": {"thread_id": "t"}},
            thread_id="t",
            result={"change_id": "x"},
            rewind_note=None,
        )
    assert merged == {"change_id": "x"}
    graph.aget_state.assert_not_called()
    plan_mock.assert_not_called()


@pytest.mark.asyncio
async def test_plan_stale_end_resume_refused_when_durable_off() -> None:
    """Ephemeral: ``plan_stale_end_resume`` не планирует restorative ``Command(goto=…)``."""
    from types import SimpleNamespace

    from workflows.checkpoint_resume import plan_stale_end_resume

    graph = MagicMock()
    snapshot = SimpleNamespace(
        next=(),
        values={
            "implementation_status": "RUNNING",
            "implementation_stage": "APPLY_IMPLEMENT",
        },
    )
    plan = await plan_stale_end_resume(
        graph,
        config={"configurable": {"thread_id": "verify-impl:visual"}},
        snapshot=snapshot,
    )
    assert plan is None


@pytest.mark.asyncio
async def test_invoke_checkpointed_redirects_to_ephemeral_when_durable_off(tmp_path: Path) -> None:
    """Прямой вызов ``invoke_checkpointed_graph`` не открывает stale resume при flag off."""
    graph = MagicMock()
    with patch(
        "workflows.checkpoint_resume.invoke_ephemeral_workflow_graph",
        AsyncMock(return_value={"verdict": "EPHEMERAL_OK"}),
    ) as ephemeral_mock:
        result = await __import__(
            "workflows.checkpoint_resume",
            fromlist=["invoke_checkpointed_graph"],
        ).invoke_checkpointed_graph(
            graph,
            initial_state={
                "change_id": "policy-test",
                "repository_path": str(tmp_path),
            },
            thread_id="verify-impl:policy-test",
        )
    ephemeral_mock.assert_awaited_once()
    assert result.get("verdict") == "EPHEMERAL_OK"
