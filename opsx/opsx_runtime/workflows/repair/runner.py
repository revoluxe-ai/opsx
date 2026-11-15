"""CLI runner for the restored LangGraph Bug Team.

Compatibility example:
  python -B opsx/opsx_runtime/workflows/repair/runner.py \
    --finding-report opsx/runs/bugbot/<run>/review.json \
    --live --allow-soft-cap

No commit/push/deploy is performed by this runner. Live agents use the
current Git worktree as ``workspace`` (no nested clone under the mission dir).
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = project_root()
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from workflows.checkpoint_resume import prepare_graph_invocation

from workflows.repair.control import materialize_review
from workflows.repair.graph import (
    build_bug_team_graph,
    build_hunt_graph,
    build_repair_graph,
)
from workflows.repair.reports import build_mission_report, build_usage_report
from workflows.repair.durable_export import persist_durable_exports
from workflows.repair.schemas import (
    DEFAULT_AGENT_INPUT_LIMIT,
    DEFAULT_MODEL_ID,
    ModelParam,
    ModelSelection,
    RepairState,
)


def _model_selection_from_args(args: argparse.Namespace) -> ModelSelection:
    params: list[ModelParam] = []
    if getattr(args, "thinking", None) and str(args.thinking).lower() != "none":
        params.append(ModelParam(id="thinking", value=args.thinking))
    if getattr(args, "reasoning_effort", None) and str(args.reasoning_effort).lower() != "none":
        params.append(ModelParam(id="reasoning_effort", value=args.reasoning_effort))
    return ModelSelection(id=args.model, params=params)


def _json_read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _state_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, RepairState):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return RepairState.model_validate(value).model_dump(mode="json")
    return RepairState.model_validate(value).model_dump(mode="json")


def _usage_summary(state: RepairState) -> dict[str, Any]:
    return build_usage_report(state).model_dump(mode="json")


def _summary(state: RepairState) -> dict[str, Any]:
    return build_mission_report(state).model_dump(mode="json")


def _find_run_dir(repo: Path, mission_id: str) -> Path:
    return resource_path(repo, "opsx", "runs", "repair", mission_id)


async def _execute(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    if args.status:
        run_dir = _find_run_dir(repo, args.status)
        latest = run_dir / "latest_state.json"
        if not latest.exists():
            raise FileNotFoundError(latest)
        print(latest.read_text(encoding="utf-8"))
        return 0

    if args.recover_state:
        source = Path(args.recover_state)
        if not source.is_absolute():
            source = resource_path(repo, source)
        previous = RepairState.model_validate(_json_read(source))
        mission_id = args.mission_id or (
            f"recovered-{previous.mission_id}-{uuid.uuid4().hex[:6]}"
        )
        run_dir = _find_run_dir(repo, mission_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        if args.live and not args.allow_soft_cap:
            raise RuntimeError(
                "--live requires --allow-soft-cap: adapter exposes "
                "usage after/during execution, not a server hard pre-run cap"
            )
        recover_updates: dict[str, Any] = {
            "mission_id": mission_id,
            "repository_path": str(repo),
            "workspace": str(repo),
            "isolated_workspace": False,
            "output_dir": str(run_dir),
            "status": "RECOVERING",
            "current_stage": args.restart_stage,
            "error": None,
            "owner_go": True if args.owner_go else previous.owner_go,
            "owner_final": args.owner_final or previous.owner_final,
            "prepare_dependencies": args.prepare_dependencies,
            "prepare_integration": args.prepare_integration,
            "require_integration": args.require_integration,
            "preflight": None,
        }
        if args.model:
            recover_updates["model"] = _model_selection_from_args(args)
            recover_updates["max_agent_input_tokens"] = args.max_agent_input_tokens
        initial = previous.model_copy(update=recover_updates)
        _json_write(run_dir / "latest_state.json", initial)
    elif args.resume:
        mission_id = args.resume
        run_dir = _find_run_dir(repo, mission_id)
        latest = RepairState.model_validate(
            _json_read(run_dir / "latest_state.json")
        )
        initial = latest.model_copy(
            update={
                "prepare_dependencies": args.prepare_dependencies,
                "prepare_integration": args.prepare_integration,
                "require_integration": args.require_integration,
            }
        )
    else:
        if not args.finding_report:
            raise ValueError("--finding-report is required for a new run")
        report_path = Path(args.finding_report)
        if not report_path.is_absolute():
            report_path = resource_path(repo, report_path)
        review = _json_read(report_path)

        mission_id = args.mission_id or f"dogfood-{uuid.uuid4().hex[:10]}"
        run_dir = _find_run_dir(repo, mission_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        if args.live and not args.allow_soft_cap:
            raise RuntimeError(
                "--live requires --allow-soft-cap: adapter exposes "
                "usage after/during execution, not a server hard pre-run cap"
            )

        queue = materialize_review(review)
        initial = RepairState(
            mission_id=mission_id,
            repository_path=str(repo),
            workspace=str(repo),
            isolated_workspace=False,
            model=_model_selection_from_args(args),
            queue=queue,
            source_review=review,
            source_report_path=str(report_path),
            max_items=args.max_items,
            max_hunts=args.max_hunts,
            max_search_rounds=args.max_search_rounds,
            max_tokens=args.max_tokens,
            max_agent_input_tokens=args.max_agent_input_tokens,
            output_dir=str(run_dir),
            owner_go=True if args.owner_go else None,
            owner_final=args.owner_final,
            prepare_dependencies=args.prepare_dependencies,
            prepare_integration=args.prepare_integration,
            require_integration=args.require_integration,
        )
        _json_write(run_dir / "latest_state.json", initial)

    if args.preflight_only:
        from workflows.repair.preflight import run_preflight

        update = await run_preflight(initial)
        final = initial.model_copy(update=update)
        _json_write(run_dir / "latest_state.json", final)
        print(
            json.dumps(
                final.preflight.model_dump(mode="json") if final.preflight else update,
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return 0 if final.status == "PREFLIGHT_READY" else 2

    if not args.live and not args.resume:
        print(
            json.dumps(
                {
                    "status": "DRY_RUN",
                    "mission_id": initial.mission_id,
                    "queue_size": len(initial.queue),
                    "model": initial.model.model_dump(mode="json"),
                    "max_agent_input_tokens": initial.max_agent_input_tokens,
                    "run_dir": initial.output_dir,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    # Import the live adapter only when execution is requested.
    from runtime_bootstrap import ensure_adapter_factories_registered

    ensure_adapter_factories_registered()
    from workflows.repair.repair_adapter_port import RepairAdapter

    adapter = RepairAdapter(timeout_seconds=args.timeout)
    checkpoint_path = run_dir / "checkpoints.sqlite"
    config = {"configurable": {"thread_id": initial.mission_id}}

    async with AsyncSqliteSaver.from_conn_string(
        str(checkpoint_path)
    ) as saver:
        if args.mode == "repair":
            graph = build_repair_graph(
                adapter,
                checkpointer=saver,
                start_at=(
                    args.restart_stage if args.recover_state else "classify"
                ),
            )
        elif args.mode == "hunt":
            graph = build_hunt_graph(adapter, checkpointer=saver)
        else:
            graph = build_bug_team_graph(adapter, checkpointer=saver)

        if args.mode == "repair" and initial.active is None:
            if not initial.queue:
                raise ValueError("repair mode requires at least one finding")
            active = initial.queue[0]
            initial = initial.model_copy(
                update={
                    "active": active,
                    "queue": initial.queue[1:],
                    "status": "CLAIMED",
                    "current_stage": "claim",
                }
            )

        invocation: Any
        if args.resume:
            if not args.owner_response:
                raise ValueError(
                    "--resume requires --owner-response for an interrupted run"
                )
            response = args.owner_response.strip().upper()
            invocation = Command(
                resume={
                    "approved": response == "GO",
                    "decision": response,
                }
            )
        else:
            invocation = await prepare_graph_invocation(
                graph,
                thread_id=initial.mission_id,
                initial_state=initial.model_dump(mode="json"),
            )
            if invocation is not None:
                snapshot = await graph.aget_state(config)
                if snapshot and snapshot.values and not snapshot.next:
                    latest_wire = _state_dict(invocation)
                    _json_write(run_dir / "latest_state.json", latest_wire)
                    persist_durable_exports(RepairState.model_validate(latest_wire))
                    print(
                        json.dumps(
                            _summary(RepairState.model_validate(latest_wire)),
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    return 0

        latest_wire = initial.model_dump(mode="json")
        try:
            async for value in graph.astream(
                invocation,
                config=config,
                stream_mode="values",
                durability="sync",
            ):
                if isinstance(value, dict) and "__interrupt__" in value:
                    continue
                latest_wire = _state_dict(value)
                _json_write(run_dir / "latest_state.json", latest_wire)
                persist_durable_exports(RepairState.model_validate(latest_wire))
        except Exception as exc:
            snapshot = await graph.aget_state(config)
            if snapshot and snapshot.values:
                latest_wire = _state_dict(snapshot.values)
            latest_wire["status"] = "BLOCKED"
            latest_wire["current_stage"] = "finish"
            latest_wire["error"] = f"{type(exc).__name__}: {exc}"
            _json_write(run_dir / "latest_state.json", latest_wire)
            persist_durable_exports(RepairState.model_validate(latest_wire))

    final = RepairState.model_validate(latest_wire)
    _json_write(run_dir / "usage.json", _usage_summary(final))
    _json_write(run_dir / "summary.json", _summary(final))
    persist_durable_exports(final)
    print(
        json.dumps(
            _summary(final),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if final.error is None else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="OPSX LangGraph repair/Bug Team runner"
    )
    p.add_argument("--repo", default=str(REPO_ROOT))
    p.add_argument("--finding-report")
    p.add_argument("--mission-id")
    p.add_argument(
        "--mode",
        choices=("bug-team", "repair", "hunt"),
        default="bug-team",
    )
    p.add_argument("--live", action="store_true")
    p.add_argument("--allow-soft-cap", action="store_true")
    p.add_argument("--preflight-only", action="store_true")
    p.add_argument(
        "--no-prepare-dependencies",
        dest="prepare_dependencies",
        action="store_false",
        help="do not pip-install missing project/test dependencies during preflight",
    )
    p.set_defaults(prepare_dependencies=True)
    p.add_argument(
        "--prepare-integration",
        action="store_true",
        help="start disposable PostgreSQL/preview PG/Redis/ClickHouse and migrate test DB",
    )
    p.add_argument(
        "--require-integration",
        action="store_true",
        help="block preflight unless the full integration capability is READY",
    )
    p.add_argument("--owner-go", action="store_true")
    p.add_argument("--owner-final")
    p.add_argument("--resume")
    p.add_argument(
        "--recover-state",
        help="Seed a new mission from an old latest_state.json",
    )
    p.add_argument(
        "--restart-stage",
        choices=("classify", "red", "owner_gate", "fix", "green", "invariant"),
        default="classify",
    )
    p.add_argument("--owner-response")
    p.add_argument("--status")
    p.add_argument("--model", default=DEFAULT_MODEL_ID)
    p.add_argument("--thinking", default="true")
    p.add_argument("--reasoning-effort", default="low")
    p.add_argument(
        "--max-agent-input-tokens",
        type=int,
        default=DEFAULT_AGENT_INPUT_LIMIT,
    )
    p.add_argument("--max-items", type=int, default=1_000_000_000)
    p.add_argument("--max-hunts", type=int, default=1_000_000_000)
    p.add_argument(
        "--max-search-rounds",
        type=int,
        default=3,
        help="after COMPLETE with FIXED: max post-close bounded-hunt verification cycles",
    )
    p.add_argument("--max-tokens", type=int, default=1_000_000_000_000)
    p.add_argument("--timeout", type=float, default=7200)
    return p


def main() -> None:
    args = parser().parse_args()
    raise SystemExit(asyncio.run(_execute(args)))


if __name__ == "__main__":
    main()
