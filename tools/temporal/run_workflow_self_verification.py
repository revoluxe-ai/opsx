#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Запуск OPSX workflow self-verification через Temporal Worker (mode=bounded).

Реальный ``_default_check_runner`` (registry/gate subprocess). Без
VerificationMockHarness и без тестовых ``_pass_runner`` / ``_blocked_pg_runner``.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BV = REPO / ".cursor" / "pipeline" / "bounded-verify"
sys.path.insert(0, str(BV))
sys.path.insert(0, str(REPO / ".cursor/pipeline/bounded-verify-tests"))
sys.path.insert(0, str(HERE))

from bounded_verify_executor import _default_check_runner  # noqa: E402
from bounded_verify_ledger import read_ledger  # noqa: E402
from bounded_verify_plan import draft_plan, freeze_plan, read_plan  # noqa: E402
from test_impl_seed import seed_hermetic_impl_ok  # noqa: E402
from verification_engine import TERMINAL_BLOCKED_CHECK, TERMINAL_SUCCESS  # noqa: E402

WORKSPACE_CHANGE_PASS = "opsx-verification-workflow-self"
WORKSPACE_CHANGE_BLOCKED = "opsx-verification-workflow-self-blocked"


def _real_runner(change_id: str) -> Callable[..., tuple[str, dict[str, Any]]]:
    def runner(check_id: str, registry_path: Path, root: Path):
        return _default_check_runner(
            check_id, registry_path, root, change_id=change_id
        )

    return runner


def _ensure_workspace_change(change_id: str, *, title: str) -> None:
    cdir = REPO / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    tasks = cdir / "tasks.md"
    if not tasks.is_file():
        tasks.write_text(f"- [x] {title}\n", encoding="utf-8")
    proposal = cdir / "proposal.md"
    if not proposal.is_file():
        proposal.write_text(f"# {title}\n", encoding="utf-8")


def _prepare_hermetic_root(
    change_id: str,
    checks: list[dict[str, Any]],
    *,
    registry_path: Path,
) -> Path:
    """Hermetic repo root: FROZEN plan + real static-check packs (symlink)."""
    tmp = Path(tempfile.mkdtemp(prefix="opsx-wf-self-"))
    _ensure_workspace_change(change_id, title="workflow self-verification contour")
    ws_change = REPO / "openspec/changes" / change_id
    dst_change = tmp / "openspec/changes" / change_id
    dst_change.mkdir(parents=True, exist_ok=True)
    for name in ("tasks.md", "proposal.md"):
        src = ws_change / name
        if src.is_file():
            shutil.copy2(src, dst_change / name)
    seed_hermetic_impl_ok(change_id, root=tmp)
    static_src = REPO / ".cursor/static-check"
    static_dst = tmp / ".cursor/static-check"
    if static_dst.is_dir() and not static_dst.is_symlink():
        shutil.rmtree(static_dst)
    static_dst.parent.mkdir(parents=True, exist_ok=True)
    if not static_dst.exists():
        static_dst.symlink_to(static_src, target_is_directory=True)
    bv_dst = tmp / ".cursor/pipeline/bounded-verify"
    bv_src = REPO / ".cursor/pipeline/bounded-verify"
    if bv_dst.is_dir() and not bv_dst.is_symlink():
        shutil.rmtree(bv_dst)
    bv_dst.parent.mkdir(parents=True, exist_ok=True)
    if not bv_dst.exists():
        bv_dst.symlink_to(bv_src, target_is_directory=True)
    existing = dst_change / "bounded-verify/plan.json"
    if existing.is_file():
        plan = read_plan(change_id, root=tmp)
        if plan.get("plan_status") == "FROZEN":
            return tmp
    draft_plan(change_id, checks, root=tmp, registry_path=registry_path)
    freeze_plan(change_id, root=tmp, registry_path=registry_path)
    return tmp


async def _run_temporal(
    *,
    repo: Path,
    change: str,
    workflow_id: str,
    runner: Callable[..., tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

    from activities import (
        ActivityConfig,
        configure_activities,
        ensure_verification_context,
        execute_assigned_check,
        execute_next_check,
        set_bounded_check_runner,
    )
    from config import TEMPORAL_TASK_QUEUE
    from workflow import VerificationExecutionWorkflow

    set_bounded_check_runner(runner)
    configure_activities(
        ActivityConfig(
            mode="bounded",
            repo_path=str(repo),
            change=change,
            lane="verification",
            bounded_registry_path=str(resource_path(repo, ".cursor/static-check/registry.yaml")),
        )
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        handle = await env.client.start_workflow(
            VerificationExecutionWorkflow.run,
            {
                "mode": "bounded",
                "repo_path": str(repo),
                "lane": "verification",
                "change": change,
            },
            id=workflow_id,
            task_queue=TEMPORAL_TASK_QUEUE,
        )
        async with Worker(
            env.client,
            task_queue=TEMPORAL_TASK_QUEUE,
            workflows=[VerificationExecutionWorkflow],
            activities=[
                ensure_verification_context,
                execute_next_check,
                execute_assigned_check,
            ],
        ):
            result = await handle.result()

    ledger = read_ledger(change, root=repo)
    plan = read_plan(change, root=repo)
    return {
        "workflow_id": workflow_id,
        "run_id": workflow_id,
        "result": result,
        "ledger_status": ledger.get("ledger_status"),
        "ledger_entries": ledger.get("entries"),
        "plan_id": plan.get("plan_id"),
        "required_checks": plan.get("required_checks"),
        "hermetic_root": str(repo),
    }


async def _main_async(args: argparse.Namespace) -> dict[str, Any]:
    reg = REPO / ".cursor/static-check/registry.yaml"
    if args.scenario == "pass":
        change = WORKSPACE_CHANGE_PASS
        checks = [
            {"check_id": "opsx-split-apply-impl-ready", "order": 1},
            {"check_id": "opsx-apply-green-fresh", "order": 2},
        ]
        workflow_id = args.workflow_id or "opsx-wf-self-verify-pass"
        expected = TERMINAL_SUCCESS
    else:
        change = WORKSPACE_CHANGE_BLOCKED
        _ensure_workspace_change(change, title="workflow self-verify BLOCKED contour")
        checks = [{"check_id": "opsx-pg-provider-receipt", "order": 1}]
        workflow_id = args.workflow_id or "opsx-wf-self-verify-blocked"
        expected = TERMINAL_BLOCKED_CHECK

    hermetic_root = _prepare_hermetic_root(change, checks, registry_path=reg)
    runner = _real_runner(change)
    evidence = await _run_temporal(
        repo=hermetic_root,
        change=change,
        workflow_id=workflow_id,
        runner=runner,
    )
    invocations = evidence["result"].get("executor_runs")
    terminal = evidence["result"].get("status")
    return {
        "ACTUAL_HEAD": args.head or "",
        "WORKFLOW_ID": evidence["workflow_id"],
        "RUN_ID": evidence["run_id"],
        "EXECUTION_MODE": "bounded",
        "VERIFICATION_PROFILE": "workflow_self_contour",
        "FROZEN_PLAN_ID": evidence["plan_id"],
        "CHANGE_ID": change,
        "REQUIRED_CHECKS": evidence["required_checks"],
        "CHECK_RESULTS": evidence["ledger_entries"],
        "LEDGER_FINAL_STATE": evidence["ledger_status"],
        "ACTUAL_EXECUTOR_INVOCATIONS": invocations,
        "TERMINAL_STATUS": terminal,
        "EXPECTED_TERMINAL": expected,
        "HERMETIC_ROOT": evidence["hermetic_root"],
        "MACHINE_CHECKABLE_EVIDENCE": str(Path(args.evidence_out)),
        "END_TO_END_VERIFICATION_PROVEN": terminal == expected,
        "BLOCKERS/RECOVERY": [] if terminal == expected else [f"terminal={terminal}"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OPSX workflow bounded self-verification")
    parser.add_argument("--scenario", choices=["pass", "blocked"], required=True)
    parser.add_argument("--workflow-id", default=None)
    parser.add_argument("--head", default=None, help="git HEAD for evidence binding")
    parser.add_argument(
        "--evidence-out",
        default="/opt/cursor/artifacts/opsx-workflow-self-verify-evidence.json",
    )
    args = parser.parse_args()
    if not args.head:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            check=False,
        )
        args.head = (proc.stdout or "").strip()
    payload = asyncio.run(_main_async(args))
    out = Path(args.evidence_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["END_TO_END_VERIFICATION_PROVEN"] else 1


if __name__ == "__main__":
    sys.exit(main())
