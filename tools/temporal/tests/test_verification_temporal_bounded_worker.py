# -*- coding: utf-8 -*-
"""Temporal Worker + bounded-verify activities (not VerificationMockHarness)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
FIXTURES = BV_ROOT.parent / "bounded-verify-tests/fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_ledger import read_ledger  # noqa: E402
from bounded_verify_plan import draft_plan, freeze_plan, read_plan  # noqa: E402
from verification_engine import TERMINAL_BLOCKED_CHECK, TERMINAL_SUCCESS  # noqa: E402


def _seed(root: Path, change_id: str) -> None:
    sys.path.insert(0, str(BV_ROOT.parent / "bounded-verify-tests"))
    from test_impl_seed import seed_hermetic_impl_ok

    cap = root / "opsx/project/capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx/project/capabilities.yaml", cap)
    for rel in (
        ".cursor/static-check/registry.yaml",
        "openspec/policies/VERIFY_POLICY.md",
        ".cursor/pipeline/bounded-verify/verification_obligations.py",
    ):
        src = REPO_ROOT / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")
    for rel in ("src/stroy", "tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    seed_hermetic_impl_ok(change_id, root=root)


def _pass_runner(check_id: str, _reg: Path, _root: Path):
    return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS", "EXIT_CODE": 0}


def _blocked_pg_runner(check_id: str, _reg: Path, _root: Path):
    return "FAIL", {"CHECK_ID": check_id, "STATUS": "BLOCKED", "EXIT_CODE": 3}


@pytest.mark.asyncio
async def test_scenario_a_frozen_plan_terminal_success(tmp_path: Path) -> None:
    """Scenario A: FROZEN bounded plan → Worker → TERMINAL_SUCCESS with ledger COMPLETE."""
    from activities import (
        ActivityConfig,
        configure_activities,
        ensure_verification_context,
        execute_assigned_check,
        execute_next_check,
    )
    from config import TEMPORAL_TASK_QUEUE
    from workflow import VerificationExecutionWorkflow

    change = "wf-bounded-pass"
    _seed(tmp_path, change)
    reg = FIXTURES / "registry_snapshot.yaml"
    checks = [
        {"check_id": "pyright-strict", "order": 1},
        {"check_id": "mypy", "order": 2},
    ]
    draft_plan(change, checks, root=tmp_path, registry_path=reg)
    freeze_plan(change, root=tmp_path, registry_path=reg)
    plan = read_plan(change, root=tmp_path)
    plan_id = plan["plan_id"]

    configure_activities(
        ActivityConfig(
            mode="bounded",
            repo_path=str(tmp_path),
            change=change,
            lane="verification",
            bounded_registry_path=str(reg),
            bounded_check_runner=_pass_runner,
        )
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        handle = await env.client.start_workflow(
            VerificationExecutionWorkflow.run,
            {
                "mode": "bounded",
                "repo_path": str(tmp_path),
                "lane": "verification",
                "change": change,
            },
            id="bounded-worker-scenario-a",
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

    assert result["status"] == TERMINAL_SUCCESS
    assert result["plan_id"] == plan_id
    assert result["executor_runs"] == 2
    ledger = read_ledger(change, root=tmp_path)
    assert ledger["ledger_status"] == "COMPLETE"
    accepted = [e.get("accepted_result_id") for e in ledger["entries"]]
    assert all(accepted)


@pytest.mark.asyncio
async def test_scenario_b_pg_provider_blocked_terminal(tmp_path: Path) -> None:
    """Scenario B: missing PG provider receipt → TERMINAL_BLOCKED_CHECK, no composer loop."""
    from activities import (
        ActivityConfig,
        configure_activities,
        ensure_verification_context,
        execute_assigned_check,
        execute_next_check,
    )
    from config import TEMPORAL_TASK_QUEUE
    from workflow import VerificationExecutionWorkflow

    change = "wf-pg-blocked"
    _seed(tmp_path, change)
    reg = tmp_path / ".cursor/static-check/registry.yaml"
    draft_plan(
        change,
        [{"check_id": "opsx-pg-provider-receipt", "order": 1}],
        root=tmp_path,
        registry_path=reg,
    )
    freeze_plan(change, root=tmp_path, registry_path=reg)

    configure_activities(
        ActivityConfig(
            mode="bounded",
            repo_path=str(tmp_path),
            change=change,
            lane="verification",
            bounded_registry_path=str(reg),
            bounded_check_runner=_blocked_pg_runner,
        )
    )

    async with await WorkflowEnvironment.start_time_skipping() as env:
        handle = await env.client.start_workflow(
            VerificationExecutionWorkflow.run,
            {
                "mode": "bounded",
                "repo_path": str(tmp_path),
                "lane": "verification",
                "change": change,
            },
            id="bounded-worker-scenario-b",
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

    assert result["status"] == TERMINAL_BLOCKED_CHECK
    assert result["executor_runs"] == 1
    ledger = read_ledger(change, root=tmp_path)
    row = ledger["entries"][0]
    assert row["check_id"] == "opsx-pg-provider-receipt"
    assert row.get("last_result") == "BLOCKED"
    assert row["state"] == "FAIL"
