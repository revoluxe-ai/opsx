"""Запуск implementation LangGraph (split-APPLY lane)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.state import GraphState
from workflows.checkpoint_resume import (
    ResolvedWorkflowRun,
    WorkflowResumeScope,
    default_run_dir,
    invoke_checkpointed_graph,
    list_checkpoint_candidates,
    score_checkpoint_thread,
    stable_thread_id,
)
from workflows.implementation.graph import ImplementationHopAgents
from workflows.main_graph import build_main_implementation_only_graph
from workflows.repo_paths import graph_repo_path_patch, resolve_opsx_repository_root


async def run_implementation_workflow(
    initial_state: GraphState,
    *,
    repo_root: Path,
    hop_agents: ImplementationHopAgents,
    run_dir: Path | None = None,
    checkpoint_path: Path | None = None,
    thread_id: str | None = None,
    resume_scope: WorkflowResumeScope | None = None,
) -> GraphState:
    """Выполнить implementation lane на parent ``build_main_graph`` (START → implementation)."""
    repo_root = resolve_opsx_repository_root(repo_root)
    initial_state = {**initial_state, **graph_repo_path_patch(initial_state, repo_root)}

    def graph_factory(checkpointer: Any) -> Any:
        return build_main_implementation_only_graph(
            hop_agents,
            checkpointer=checkpointer,
        )

    if resume_scope is None and thread_id is None:
        change_id = str(initial_state.get("change_id") or "")
        if change_id:
            resume_scope = WorkflowResumeScope(kind="implement", change_id=change_id)
    if thread_id is None:
        thread_id = ""

    if resume_scope is not None:
        run_dir_resolved = default_run_dir(repo_root, resume_scope)
        run_dir_resolved.mkdir(parents=True, exist_ok=True)
        candidates = list_checkpoint_candidates(repo_root, resume_scope)
        resolved: ResolvedWorkflowRun | None = None
        best_score = -1
        for ckpt_path, tid in candidates:
            async with AsyncSqliteSaver.from_conn_string(str(ckpt_path)) as saver:
                graph = graph_factory(saver)
                scored = await score_checkpoint_thread(graph, thread_id=tid)
            if scored is None:
                continue
            score, next_nodes = scored
            if score <= best_score:
                continue
            best_score = score
            resolved = ResolvedWorkflowRun(
                run_dir=ckpt_path.parent,
                checkpoint_path=ckpt_path,
                thread_id=tid,
                resumed=bool(next_nodes),
            )
        if resolved is None:
            checkpoint_path = run_dir_resolved / "checkpoints.sqlite"
            thread_id = stable_thread_id(resume_scope)
            run_dir = run_dir_resolved
        else:
            checkpoint_path = resolved.checkpoint_path
            thread_id = resolved.thread_id
            run_dir = resolved.run_dir
    elif checkpoint_path is None:
        if run_dir is None:
            raise ValueError("run_dir or checkpoint_path is required")
        checkpoint_path = run_dir / "checkpoints.sqlite"

    checkpoint_path = checkpoint_path.resolve()
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        graph = graph_factory(saver)
        return await invoke_checkpointed_graph(
            graph,
            initial_state=initial_state,
            thread_id=thread_id,
            repo_root=repo_root,
        )
