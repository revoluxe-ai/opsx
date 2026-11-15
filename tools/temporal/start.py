#!/usr/bin/env python3
"""Entry point for the isolated Temporal review/repair loop POC.

Default lane is bugbot. Pass `--lane security` or `--lane project-bug-hunt`.
All lanes use the same Temporal queue; restart one worker after code changes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("PYTHONUTF8", "1")

from config import (
    PROJECT_BUG_HUNT_DEFAULT_BASE,
    PROJECT_BUG_HUNT_DEFAULT_CHANGE,
    PROJECT_BUG_HUNT_DEFAULT_HEAD,
    TEMPORAL_TASK_QUEUE,
)
from cursor_adapter import MockCursorAdapter, build_scenario
from sdk_cursor_adapter import make_sdk_cursor_adapter
from lanes import (
    BUGBOT_LANE,
    PROJECT_BUG_HUNT_LANE_ID,
    LaneSpec,
    get_lane,
)
from loop_engine import ReviewRepairLoop
from model_config import DEFAULT_MODEL_CONFIG_PATH, AgentModelConfig
from singleton import stable_workflow_id


_TERMINAL_OK = frozenset(
    {
        "BUGBOT_CLEAN",
        "SECURITY_CLEAN",
        "HUNT_CLEAN",
        "NO_REPAIR_PROGRESS",
        "MAX_ROUNDS_REACHED",
        "STALLED",
        "ALL_REQUIRED_CHECKS_PASS",
        "VERIFICATION_EXECUTION_COMPLETE",
    }
)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _print_json(payload: dict[str, Any] | list[Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        print(text)
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True, indent=2))


def _load_model_config(path: str | None = None) -> AgentModelConfig:
    return AgentModelConfig.load(path or DEFAULT_MODEL_CONFIG_PATH)


def _resolve_model_config_path(
    model_config: str | None,
    review_tier: str | None,
    repair_tier: str | None,
    repo: str,
) -> str:
    if review_tier or repair_tier:
        if not review_tier or not repair_tier:
            raise SystemExit("Both --review-tier and --repair-tier are required together")
        from model_config import write_step_overlay

        from run_store import durable_config_path

        dest = durable_config_path(repo)
        write_step_overlay(review=review_tier, repair=repair_tier, dest=dest)
        return str(dest)
    return model_config or str(DEFAULT_MODEL_CONFIG_PATH)


def _print_model_config(model_config: AgentModelConfig) -> None:
    _print_json(model_config.summary())


def _print_outcome(outcome) -> None:
    print(f"STATUS: {outcome.status}")
    print(f"REPAIR_ROUNDS: {outcome.repair_rounds}")
    print(f"REVIEW_RUNS: {outcome.review_runs}")
    if outcome.session_id:
        print(f"SESSION_ID: {outcome.session_id}")
    if outcome.message:
        print(f"MESSAGE: {outcome.message}")


def _default_mock_scenario(lane: LaneSpec | str, requested: str | None) -> str:
    if requested:
        return requested
    lane_id = lane if isinstance(lane, str) else lane.id
    if lane_id == "security":
        return "security-scenario1"
    if lane_id == PROJECT_BUG_HUNT_LANE_ID:
        return "project-bug-hunt-scenario1"
    if lane_id == "verification":
        return "verification-all-pass"
    return "scenario1"


def _resolve_workflow_cls(lane: LaneSpec | str):
    from workflow import (
        BugbotRepairWorkflow,
        ProjectBugHuntWorkflow,
        SecurityReviewWorkflow,
        VerificationExecutionWorkflow,
    )

    lane_id = lane if isinstance(lane, str) else lane.id
    if lane_id == BUGBOT_LANE.id:
        return BugbotRepairWorkflow
    if lane_id == "security":
        return SecurityReviewWorkflow
    if lane_id == PROJECT_BUG_HUNT_LANE_ID:
        return ProjectBugHuntWorkflow
    if lane_id == "verification":
        return VerificationExecutionWorkflow
    raise ValueError(f"No Temporal workflow registered for lane {lane_id!r}")


def run_mock_direct(mock_scenario: str, lane: LaneSpec) -> int:
    adapter = MockCursorAdapter(build_scenario(mock_scenario))
    outcome = ReviewRepairLoop(adapter, lane=lane, mock_mode=True).run()
    _print_outcome(outcome)
    return 0 if outcome.status in _TERMINAL_OK else 1


def run_real_direct(repo_path: str, lane: LaneSpec, model_config_path: str | None = None) -> int:
    model_config = _load_model_config(model_config_path)
    _print_model_config(model_config)
    workflow_key = f"start.py:direct:{lane.id}"
    adapter = make_sdk_cursor_adapter(
        repo_path,
        workflow_key=workflow_key,
        model_config=model_config,
    )
    adapter.bootstrap_agent()
    outcome = ReviewRepairLoop(adapter, lane=lane, repo_path=repo_path).run()
    _print_outcome(outcome)
    return 0 if outcome.status == lane.clean_status else 1


async def run_via_temporal(
    mode: str,
    repo_path: str,
    mock_scenario: str | None,
    model_config_path: str | None,
    lane: LaneSpec | str,
    *,
    base: str | None,
    head: str | None,
    change: str | None,
) -> int:
    from datetime import timedelta

    from activities import ActivityConfig, WorkflowRunConfig, configure_activities
    from config import TEMPORAL_UI_URL
    from temporal_client import connect_temporal_client
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError

    client = await connect_temporal_client()

    model_config = _load_model_config(model_config_path)
    _print_model_config(model_config)

    lane_id = lane if isinstance(lane, str) else lane.id
    workflow_id_prefix = (
        lane if isinstance(lane, str) else None
    )
    if isinstance(lane, LaneSpec):
        workflow_id_prefix = lane.workflow_id_prefix
        review_activity = lane.review_activity
        repair_activity = lane.repair_activity
    elif lane_id == "verification":
        workflow_id_prefix = "verification-execution-poc"
        review_activity = "execute_next_check"
        repair_activity = "execute_assigned_check"
    else:
        raise ValueError(f"Unsupported lane {lane_id!r}")

    configure_activities(
        ActivityConfig(
            mode=mode,
            repo_path=repo_path,
            mock_scenario=mock_scenario,
            model_config_path=str(model_config.source_path) if model_config.source_path else None,
            lane=lane_id,
            base=base,
            head=head,
            change=change,
        )
    )

    workflow_cls = _resolve_workflow_cls(lane)
    change_for_id = change if lane_id in ("project-bug-hunt", "verification") else None
    workflow_id = stable_workflow_id(workflow_id_prefix, mode, change=change_for_id)
    try:
        handle = await client.start_workflow(
            workflow_cls.run,
            WorkflowRunConfig(
                mode=mode,
                repo_path=repo_path,
                mock_scenario=mock_scenario,
                lane=lane_id,
                base=base,
                head=head,
                change=change,
            ),
            id=workflow_id,
            task_queue=TEMPORAL_TASK_QUEUE,
            task_timeout=timedelta(seconds=60),
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
    except WorkflowAlreadyStartedError as exc:
        existing = exc.workflow_id or workflow_id
        print("STATUS: BLOCKED")
        print("REASON: PIPELINE_ALREADY_RUNNING")
        print(f"WORKFLOW_ID: {existing}", flush=True)
        print(f"LANE: {lane_id}", flush=True)
        print(
            f"TEMPORAL_UI: {TEMPORAL_UI_URL}/namespaces/default/workflows/{existing}",
            flush=True,
        )
        print(
            "MESSAGE: Identical pipeline is already RUNNING. Wait for it to finish; "
            "do not start a second PP. Terminate in Temporal UI only if the first is stuck."
        )
        return 2
    print(f"WORKFLOW_ID: {workflow_id}", flush=True)
    print(f"LANE: {lane_id}", flush=True)
    print(
        f"TEMPORAL_UI: {TEMPORAL_UI_URL}/namespaces/default/workflows/{workflow_id}",
        flush=True,
    )
    if lane_id == "verification":
        print(
            "Track steps in UI: ensure_verification_context -> "
            "execute_next_check -> execute_assigned_check -> ..."
        )
    else:
        print(
            "Track steps in UI: ensure_cursor_session -> "
            f"{review_activity} -> {repair_activity} -> "
            "execute_commit_materialize -> ..."
        )
    result = await handle.result()
    from run_store import durable_runs_dir

    run_dump = durable_runs_dir(repo_path) / f"{workflow_id}.json"
    run_dump.parent.mkdir(parents=True, exist_ok=True)
    run_dump.write_text(json.dumps(result, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(f"RESULT_FILE: {run_dump}")
    _print_json(result)
    ok = result.get("status") in _TERMINAL_OK or result.get("temporal_status") in _TERMINAL_OK
    return 0 if ok else 1


def run_verification_mock_direct(mock_scenario: str, change: str, repo_path: str) -> int:
    from verification_loop import VerificationExecutionLoop
    from verification_mock import VerificationMockHarness, clear_mock_harnesses

    clear_mock_harnesses()
    harness = VerificationMockHarness.from_scenario(
        mock_scenario, change=change, repo=repo_path
    )
    outcome = VerificationExecutionLoop(
        change=change,
        run_next_check=harness.run_next_check,
        run_assigned_check=harness.run_assigned_check,
    ).run()
    _print_json(outcome.to_dict())
    return 0 if outcome.status == "ALL_REQUIRED_CHECKS_PASS" else 1


def probe_cursor(repo_path: str) -> int:
    from cursor_adapter import RealCursorAdapter

    evidence = RealCursorAdapter.probe(repo_path)
    docs_dir = Path(__file__).resolve().parent / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    out = docs_dir / "CURSOR_CLI_EXPERIMENT.json"
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_json(evidence)
    return 0 if evidence.get("session_continuation") == "WORKING" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Temporal review/repair loop POC")
    parser.add_argument("--mode", choices=["mock", "real", "temporal-mock", "temporal-real", "probe", "show-models"], required=True)
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument(
        "--lane",
        choices=["bugbot", "security", "project-bug-hunt", "verification"],
        default="bugbot",
    )
    parser.add_argument("--base", default=PROJECT_BUG_HUNT_DEFAULT_BASE)
    parser.add_argument("--head", default=PROJECT_BUG_HUNT_DEFAULT_HEAD)
    parser.add_argument("--change", default=PROJECT_BUG_HUNT_DEFAULT_CHANGE)
    parser.add_argument("--mock-scenario", default=None)
    parser.add_argument("--model-config", default=str(DEFAULT_MODEL_CONFIG_PATH))
    parser.add_argument("--review-tier", default=None, help="low|medium|high; requires --repair-tier")
    parser.add_argument("--repair-tier", default=None, help="low|medium|high; requires --review-tier")
    parser.add_argument("--use-temporal", action="store_true", help="Run through Temporal client/worker")
    args = parser.parse_args(argv)
    _configure_stdio()
    args.model_config = _resolve_model_config_path(
        args.model_config,
        args.review_tier,
        args.repair_tier,
        args.repo,
    )

    if args.lane == "verification":
        if not (args.change or "").strip():
            raise SystemExit("verification lane requires --change")
        lane: LaneSpec | str = "verification"
        mock_scenario = _default_mock_scenario(lane, args.mock_scenario)
    elif args.lane == PROJECT_BUG_HUNT_LANE_ID:
        lane = get_lane(args.lane, base=args.base, head=args.head, change=args.change)
        mock_scenario = _default_mock_scenario(lane, args.mock_scenario)
    elif args.lane == "security":
        lane = get_lane(args.lane, base=args.base)
        mock_scenario = _default_mock_scenario(lane, args.mock_scenario)
    else:
        lane = get_lane(args.lane)
        mock_scenario = _default_mock_scenario(lane, args.mock_scenario)

    if args.mode == "show-models":
        _print_model_config(_load_model_config(args.model_config))
        return 0

    if args.mode == "probe":
        return probe_cursor(args.repo)

    if args.mode == "mock":
        if args.lane == "verification":
            return run_verification_mock_direct(mock_scenario, args.change, args.repo)
        assert isinstance(lane, LaneSpec)
        return run_mock_direct(mock_scenario, lane)

    if args.mode == "real":
        if args.lane == "verification":
            raise SystemExit(
                "verification real mode requires Temporal "
                "(--mode temporal-real); direct real loop not supported"
            )
        assert isinstance(lane, LaneSpec)
        return run_real_direct(args.repo, lane, args.model_config)

    if args.mode == "temporal-mock":
        return asyncio.run(
            run_via_temporal(
                "mock",
                args.repo,
                mock_scenario,
                args.model_config,
                lane,
                base=args.base,
                head=args.head,
                change=args.change,
            )
        )

    if args.mode == "temporal-real":
        return asyncio.run(
            run_via_temporal(
                "real",
                args.repo,
                None,
                args.model_config,
                lane,
                base=args.base,
                head=args.head,
                change=args.change,
            )
        )

    return 1


if __name__ == "__main__":
    sys.exit(main())
