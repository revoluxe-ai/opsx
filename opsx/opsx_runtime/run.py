"""OPSX main entry point: complete one-node review of a real committed branch.

Invoke from any directory:
    python opsx/opsx_runtime/run.py

No diff is fed into the prompt: Bugbot itself inspects Git using Cursor tools.
Review runs in the current Git worktree (no nested clone under the run dir).
"""

from __future__ import annotations

from opsx.project_context import project_root

import argparse
import asyncio
import json
import subprocess
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parent
REPO_ROOT = project_root()
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

from workflows.checkpoint_resume import invoke_checkpointed_graph  # noqa: E402
from agents.adapters.bridge_runtime_port import open_bridge_runtime  # noqa: E402
from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered  # noqa: E402
from agents.adapters.provider_registry import build_review_adapter  # noqa: E402
from agents.abc_agent import ABCAgent  # noqa: E402
from agents.state import GraphState  # noqa: E402
from agents.verify.bugbot.agent import BugBotAgent  # noqa: E402
from workflows.verify.bugbot.bugbot_pipeline.bugbot_graph import (  # noqa: E402
    build_bugbot_graph,
)  # noqa: E402


HAIKU_LOW: dict[str, Any] = {
    "id": "claude-haiku-5-5",
    "params": [
        {"id": "thinking", "value": "true"},
        {"id": "context", "value": "300k"},
        {"id": "reasoning_effort", "value": "low"},
    ],
}


def git(cwd: Path, *arguments: str, timeout: float = 90) -> str:
    process = subprocess.run(
        ["git", *arguments], cwd=str(cwd), check=True,
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )
    return process.stdout.strip()


def dump_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(".tmp")
    staging.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    staging.replace(path)


async def _run_bugbot_workflow(
    initial_state: GraphState,
    *,
    agent: ABCAgent,
    run_dir: Path,
    thread_id: str,
) -> GraphState:
    """Отдельный Bugbot-граф (не главный OPSX verify-workflow)."""
    checkpoint_path = run_dir / "checkpoints.sqlite"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        graph = build_bugbot_graph(agent, checkpointer=saver)
        return await invoke_checkpointed_graph(
            graph,
            initial_state=initial_state,
            thread_id=thread_id,
        )


def resolve_review_scope(source: Path) -> dict[str, Any]:
    """Validate branch/stage merge-base and return Git scope metadata (no clone)."""
    source = source.resolve()
    if Path(git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        raise ValueError("Repository must point to the Git working-tree root")
    branch = git(source, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch or branch == "stage":
        raise ValueError("Review requires a feature branch other than stage")
    head = git(source, "rev-parse", "HEAD")
    base = git(source, "rev-parse", "stage")
    if not git(source, "merge-base", "stage", "HEAD"):
        raise ValueError("HEAD and stage have no merge-base")
    dirty = len(git(source, "status", "--porcelain=v1", "-uno").splitlines())
    return {
        "branch": branch,
        "head": head,
        "stage": base,
        "dirty_tracked_files_in_workspace": dirty,
    }


async def execute(
    source: Path = REPO_ROOT, *,
    timeout_seconds: float = 900,
    dry_run: bool = False,
) -> dict[str, Any]:
    ensure_adapter_factories_registered()
    source = source.resolve()
    run_dir = source / "opsx" / "runs" / "bugbot" / (
        "real-" + uuid.uuid4().hex[:12]
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "review.json"
    execution_path = run_dir / "execution.json"
    thread_id = run_dir.name
    adapter = build_review_adapter(
        timeout_seconds=timeout_seconds,
        allow_git_shell=False,
    )
    meta: dict[str, Any] = {
        "thread_id": thread_id,
        "execution_id": thread_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_repository": str(source),
        "workspace": str(source),
        "scope": "complete committed project branch versus stage; agent computes diff",
        "model": HAIKU_LOW,
        "execution_status": "PREPARING",
        "usage": None,
        "usage_source": "unavailable",
        "cost": None,
        "cost_status": "not_reported",
        "review_path": str(report_path),
        "checkpoint_path": str(run_dir / "checkpoints.sqlite"),
        "prompt_source": "agents/verify/bugbot/prompt/get_prompt.py (unchanged)",
        "prepared_diff": False,
    }
    dump_json(execution_path, meta)
    print("RUN_DIR:", run_dir, flush=True)
    try:
        scope = await asyncio.to_thread(resolve_review_scope, source)
        meta.update(scope)
        meta["execution_status"] = "PREPARED"
        dump_json(execution_path, meta)
        print("SCOPE:", json.dumps(scope), flush=True)

        if dry_run:
            meta["execution_status"] = "DRY_RUN"
            return meta

        meta["execution_status"] = "RUNNING"
        dump_json(execution_path, meta)
        initial_state = {
            "repository_path": str(source),
            "workspace": str(source),
            "model": HAIKU_LOW,
            "isolated_workspace": False,
            "stream_log": str(run_dir / "bugbot.live.ndjson"),
            "stream_label": thread_id,
        }
        # No external Git diff payload and no appended prompt instructions.
        async with open_bridge_runtime(
            source, store_scope=f"bugbot:{thread_id}"
        ) as bridge:
            adapter = build_review_adapter(
                timeout_seconds=timeout_seconds, allow_git_shell=False,
                bridge_runtime=bridge,
            )
            state = await _run_bugbot_workflow(
                initial_state,
                agent=BugBotAgent(adapter),
                run_dir=run_dir,
                thread_id=thread_id,
            )
        review = state["review"]
        dump_json(report_path, review)
        meta.update(
            execution_status=state["execution_status"],
            validation_status="VALIDATED_JSON",
            review_status=review["status"],
            findings_count=len(review["findings"]),
            agent_id=state["agent_id"],
            run_id=state["run_id"],
            usage=state["usage"],
            usage_source=state["usage_source"],
            cost=state["cost"],
            cost_status=state["cost_status"],
            billing_error=state.get("billing_error"),
        )
    except Exception as exc:
        meta.update(
            execution_status="FAILED",
            error=f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
        )
        observed = adapter.last_result
        if observed is not None:
            meta.update(
                run_id=observed.run_id,
                agent_id=observed.agent_id,
                sdk_status=observed.status,
                usage=observed.usage,
                usage_source=observed.usage_source,
                cost=observed.cost,
                cost_status=observed.cost_status,
                billing_error=observed.billing_error,
            )
            (run_dir / "raw-response.txt").write_text(
                observed.text, encoding="utf-8"
            )
    finally:
        meta["finished_at"] = datetime.now(timezone.utc).isoformat()
        dump_json(execution_path, meta)
        print(
            "EXECUTION_SUMMARY:",
            json.dumps(
                {
                    k: meta.get(k)
                    for k in (
                        "execution_status", "review_status",
                        "findings_count", "run_id",
                        "usage", "cost", "cost_status", "error"
                    )
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return meta


async def run(
    workspace: Path = REPO_ROOT,
    *,
    timeout: float = 900,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Public orchestration API used by tests and the CLI."""
    return await execute(workspace, timeout_seconds=timeout, dry_run=dry_run)


async def reconcile_saved_run(run_dir: Path) -> dict[str, Any]:
    """Validate an existing SDK reply through the SAME graph, without SDK calls."""
    from typing import Mapping, Sequence
    from agents.adapters.abc_adapter import ABCAdapter, AgentResult

    class SavedResponseAdapter(ABCAdapter):
        async def _run_turn(
            self,
            state: Mapping[str, Any],
            *,
            prompt: str,
            tools: Sequence[str],
            payload_model: Any = None,
        resume_agent_id: str | None = None,
        ) -> AgentResult:
            return AgentResult(
                text=raw_text,
                status=str(meta["sdk_status"]),
                agent_id=str(meta["agent_id"]),
                run_id=str(meta["run_id"]),
                usage=meta.get("usage"),
                cost=meta.get("cost"),
                usage_source=str(meta.get("usage_source", "unavailable")),
                cost_status=str(meta.get("cost_status", "pending")),
                billing_error=meta.get("billing_error"),
            )

    run_dir = run_dir.resolve()
    execution_path = run_dir / "execution.json"
    meta = json.loads(execution_path.read_text(encoding="utf-8"))
    raw_text = (run_dir / "raw-response.txt").read_text(encoding="utf-8")
    if not meta.get("run_id") or meta.get("sdk_status") != "finished":
        raise ValueError("No completed SDK response to reconcile")

    workspace = Path(str(meta.get("workspace") or meta.get("source_repository")))
    state = await _run_bugbot_workflow(
        {
            "repository_path": str(workspace),
            "workspace": str(workspace),
            "model": meta["model"],
            "isolated_workspace": False,
        },
        agent=BugBotAgent(SavedResponseAdapter()),
        run_dir=run_dir,
        thread_id=meta["thread_id"],
    )
    review = state["review"]
    dump_json(run_dir / "review.json", review)
    meta.update(
        execution_status=state["execution_status"],
        validation_status="VALIDATED_JSON_FROM_SAVED_SDK_REPLY",
        review_status=review["status"],
        findings_count=len(review["findings"]),
        original_validation_error=meta.pop("error", None),
        reconciled_at=datetime.now(timezone.utc).isoformat(),
    )
    meta.pop("traceback", None)
    dump_json(execution_path, meta)
    print(
        "RECONCILED:",
        json.dumps(
            {
                "execution_status": meta["execution_status"],
                "review_status": meta["review_status"],
                "findings_count": meta["findings_count"],
                "usage": meta.get("usage"),
                "cost": meta.get("cost"),
                "cost_status": meta.get("cost_status"),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return meta


def main() -> None:
    cli = argparse.ArgumentParser(description="OPSX real-project Bugbot")
    cli.add_argument("--repo", type=Path, default=REPO_ROOT)
    cli.add_argument("--timeout", type=float, default=900)
    cli.add_argument("--dry-run", action="store_true")
    cli.add_argument("--reconcile", type=Path)
    args = cli.parse_args()
    if args.reconcile:
        outcome = asyncio.run(reconcile_saved_run(args.reconcile))
    else:
        outcome = asyncio.run(
            execute(
                args.repo,
                timeout_seconds=args.timeout,
                dry_run=args.dry_run,
            )
        )
    if outcome.get("execution_status", "").lower() not in {
        "finished", "completed", "success", "succeeded", "dry_run"
    }:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
