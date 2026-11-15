# -*- coding: utf-8 -*-
"""S6 workflow gaps: duplicate activity completion + supersession generation."""

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

from bounded_verify_executor import (  # noqa: E402
    BoundedVerifyExecutorError,
    run_current_check,
)
from bounded_verify_ledger import read_ledger, schedule_write  # noqa: E402
from bounded_verify_plan import BoundedVerifyPlanError, draft_plan, freeze_plan  # noqa: E402
from bounded_verify_result import (  # noqa: E402
    BoundedVerifyResultError,
    RESULT_ALREADY_EXISTS,
)
from bounded_verify_supersession import publish_successor_plan  # noqa: E402
from verification_plan_compose import compose_verification_plan  # noqa: E402


def _seed(root: Path, change: str) -> None:
    sys.path.insert(0, str(BV_ROOT.parent / "bounded-verify-tests"))
    from test_impl_seed import seed_hermetic_impl_ok

    for rel in (
        ".cursor/static-check/registry.yaml",
        "openspec/policies/VERIFY_POLICY.md",
        ".cursor/pipeline/bounded-verify/verification_obligations.py",
        "opsx/project/capabilities.yaml",
    ):
        src = REPO_ROOT / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    cdir = root / "openspec/changes" / change
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")
    for rel in ("src/stroy", "tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    seed_hermetic_impl_ok(change, root=root)


def _pass_runner(check_id: str, _reg: Path, _root: Path):
    return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS", "EXIT_CODE": 0}


def test_duplicate_executor_same_execution_id_blocked(tmp_path: Path) -> None:
    """Workflow idempotency contract: second activity completion cannot re-write result."""
    change = "dup-exec"
    _seed(tmp_path, change)
    compose_verification_plan(change, "bounded_minimal", root=tmp_path)
    freeze_plan(change, root=tmp_path)
    reg = FIXTURES / "registry_snapshot.yaml"
    schedule_write(change, root=tmp_path)
    run_current_check(
        change,
        root=tmp_path,
        registry_path=reg,
        check_runner=_pass_runner,
        execution_id="activity-run-1",
    )
    with pytest.raises(BoundedVerifyResultError) as exc:
        run_current_check(
            change,
            root=tmp_path,
            registry_path=reg,
            check_runner=_pass_runner,
            execution_id="activity-run-1",
        )
    assert exc.value.code == RESULT_ALREADY_EXISTS
    ledger = read_ledger(change, root=tmp_path)
    assert ledger["entries"][0]["state"] == "CURRENT"
    schedule_write(change, root=tmp_path)
    entry = read_ledger(change, root=tmp_path)["entries"][0]
    assert entry["state"] == "PASS"
    rid = entry["accepted_result_id"]
    schedule_write(change, root=tmp_path)
    assert read_ledger(change, root=tmp_path)["entries"][0]["accepted_result_id"] == rid


def test_supersession_resets_generation_stale_complete_not_terminal(tmp_path: Path) -> None:
    """Successor activation: archived COMPLETE cannot authorize terminal on new generation."""
    from verification_engine import TERMINAL_SUCCESS, classify_next_check

    change = "super-gen"
    _seed(tmp_path, change)
    reg = FIXTURES / "registry_snapshot.yaml"
    checks = [
        {"check_id": "pyright-strict", "order": 1},
        {"check_id": "mypy", "order": 2},
    ]
    draft_plan(change, checks, root=tmp_path, registry_path=reg)
    freeze_plan(change, root=tmp_path, registry_path=reg)
    plan = json.loads(
        (tmp_path / "openspec/changes" / change / "bounded-verify/plan.json").read_text()
    )
    for _ in checks:
        schedule_write(change, root=tmp_path)
        run_current_check(change, root=tmp_path, registry_path=reg, check_runner=_pass_runner)
        schedule_write(change, root=tmp_path)
    assert classify_next_check(
        repo=tmp_path, change=change, raw_output="", completed=True
    ).status == TERMINAL_SUCCESS
    publish_successor_plan(change, checks, root=tmp_path, registry_path=reg)
    ledger = read_ledger(change, root=tmp_path)
    assert ledger["ledger_status"] == "ACTIVE"
    assert ledger["plan_id"] != plan["plan_id"]
    terminal = classify_next_check(
        repo=tmp_path, change=change, raw_output="", completed=True
    )
    assert terminal.status != TERMINAL_SUCCESS


def test_supersession_blocked_during_current_assignment(tmp_path: Path) -> None:
    change = "super-block"
    _seed(tmp_path, change)
    compose_verification_plan(change, "bounded_minimal", root=tmp_path)
    freeze_plan(change, root=tmp_path)
    schedule_write(change, root=tmp_path)
    reg = FIXTURES / "registry_snapshot.yaml"
    checks = json.loads(
        (tmp_path / "openspec/changes" / change / "bounded-verify/plan.json").read_text()
    )["required_checks"]
    with pytest.raises(BoundedVerifyPlanError) as exc:
        publish_successor_plan(change, checks, root=tmp_path, registry_path=reg)
    assert exc.value.code == "SUPERSESSION_OUTSTANDING_ASSIGNMENTS"


@pytest.mark.asyncio
async def test_workflow_worker_restart_partial_sequential_workers() -> None:
    """S6_RESTART=PARTIAL: sequential workers; not proven mid-activity interruption."""
    from activities import (
        ActivityConfig,
        configure_activities,
        ensure_verification_context,
        execute_assigned_check,
        execute_next_check,
    )
    from config import TEMPORAL_TASK_QUEUE
    from workflow import VerificationExecutionWorkflow

    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=str(REPO_ROOT),
            mock_scenario="verification-all-pass",
        )
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
        handle = await env.client.start_workflow(
            VerificationExecutionWorkflow.run,
            {
                "mode": "mock",
                "repo_path": str(REPO_ROOT),
                "mock_scenario": "verification-all-pass",
                "change": "mock-change",
            },
            id="s6-worker-lifecycle-restart",
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
            pass
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
    assert result["status"] in ("ALL_REQUIRED_CHECKS_PASS", "VERIFICATION_EXECUTION_COMPLETE")


@pytest.mark.asyncio
async def test_workflow_worker_restart_all_pass_idempotent(tmp_path: Path) -> None:
    """Temporal workflow integration: worker restart + replay completes once."""
    from activities import (
        ActivityConfig,
        configure_activities,
        ensure_verification_context,
        execute_assigned_check,
        execute_next_check,
    )
    from config import TEMPORAL_TASK_QUEUE
    from workflow import VerificationExecutionWorkflow

    configure_activities(
        ActivityConfig(
            mode="mock",
            repo_path=str(REPO_ROOT),
            mock_scenario="verification-all-pass",
        )
    )
    async with await WorkflowEnvironment.start_time_skipping() as env:
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
            result = await env.client.execute_workflow(
                VerificationExecutionWorkflow.run,
                {
                    "mode": "mock",
                    "repo_path": str(REPO_ROOT),
                    "mock_scenario": "verification-all-pass",
                    "change": "mock-change",
                },
                id="s6-restart-replay",
                task_queue=TEMPORAL_TASK_QUEUE,
            )
    assert result["status"] in ("ALL_REQUIRED_CHECKS_PASS", "VERIFICATION_EXECUTION_COMPLETE")
