# -*- coding: utf-8 -*-
"""S6: isolated Temporal + bounded ledger replay/crash matrix (no live server)."""

from __future__ import annotations

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
    ASSIGNMENT_MISMATCH,
    BoundedVerifyExecutorError,
    run_current_check,
)
from bounded_verify_ledger import NO_PENDING_CHECKS, read_ledger, schedule_write  # noqa: E402
from bounded_verify_plan import draft_plan, freeze_plan  # noqa: E402
from verification_engine import TERMINAL_FAIL, TERMINAL_SUCCESS  # noqa: E402


def _seed(root: Path, change_id: str) -> None:
    sys.path.insert(0, str(BV_ROOT.parent / "bounded-verify-tests"))
    from test_impl_seed import seed_hermetic_impl_ok

    cap = root / "opsx/project/capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx/project/capabilities.yaml", cap)
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    for rel in ("src/stroy", "tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    seed_hermetic_impl_ok(change_id, root=root)


def _pass_runner(check_id: str, _reg: Path, _root: Path):
    return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS", "EXIT_CODE": 0}


def _freeze(root: Path, change: str) -> None:
    reg = FIXTURES / "registry_snapshot.yaml"
    checks = [
        {"check_id": "pyright-strict", "order": 1},
        {"check_id": "mypy", "order": 2},
    ]
    draft_plan(change, checks, root=root, registry_path=reg)
    freeze_plan(change, root=root, registry_path=reg)


def _freeze_one_check(root: Path, change: str) -> None:
    reg = FIXTURES / "registry_snapshot.yaml"
    draft_plan(
        change,
        [{"check_id": "pyright-strict", "order": 1}],
        root=root,
        registry_path=reg,
    )
    freeze_plan(change, root=root, registry_path=reg)


def test_isolated_crash_before_ledger_replace_replay(tmp_path: Path) -> None:
    """Ledger integration: crash hook then idempotent schedule_write replay."""
    change = "s6-crash"
    _seed(tmp_path, change)
    _freeze_one_check(tmp_path, change)
    reg = FIXTURES / "registry_snapshot.yaml"
    schedule_write(change, root=tmp_path)
    run_current_check(change, root=tmp_path, registry_path=reg, check_runner=_pass_runner)
    rev_before = int(read_ledger(change, root=tmp_path)["ledger_revision"])
    try:
        schedule_write(
            change,
            root=tmp_path,
            test_hook={"crash_before_replace": True},
        )
    except Exception:
        pass
    assert int(read_ledger(change, root=tmp_path)["ledger_revision"]) == rev_before
    replay = schedule_write(change, root=tmp_path)
    assert replay.code == NO_PENDING_CHECKS
    assert read_ledger(change, root=tmp_path)["entries"][0]["state"] == "PASS"
    assert read_ledger(change, root=tmp_path)["entries"][0].get("accepted_result_id")


def test_isolated_stale_assignment_epoch(tmp_path: Path) -> None:
    change = "s6-stale-epoch"
    _seed(tmp_path, change)
    _freeze(tmp_path, change)
    reg = FIXTURES / "registry_snapshot.yaml"
    schedule_write(change, root=tmp_path)
    with pytest.raises(BoundedVerifyExecutorError) as exc:
        run_current_check(
            change,
            root=tmp_path,
            registry_path=reg,
            check_runner=_pass_runner,
            assignment_epoch=0,
        )
    assert exc.value.code == ASSIGNMENT_MISMATCH


@pytest.mark.asyncio
async def test_workflow_mock_fail_stop(tmp_path: Path) -> None:
    """Temporal workflow integration (time-skipping): second-check FAIL."""
    from activities import (
        ActivityConfig,
        configure_activities,
        ensure_verification_context,
        execute_assigned_check,
        execute_next_check,
    )
    from config import TEMPORAL_TASK_QUEUE
    from verification_mock import VerificationMockHarness, clear_mock_harnesses
    from verification_loop import VerificationExecutionLoop
    from workflow import VerificationExecutionWorkflow

    clear_mock_harnesses()
    harness = VerificationMockHarness.from_scenario(
        "verification-second-fail", change="mock-change", repo=tmp_path
    )
    outcome = VerificationExecutionLoop(
        change="mock-change",
        run_next_check=harness.run_next_check,
        run_assigned_check=harness.run_assigned_check,
    ).run()
    assert outcome.status == TERMINAL_FAIL

    configure_activities(
        ActivityConfig(mode="mock", repo_path=str(REPO_ROOT), mock_scenario="verification-second-fail")
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
                    "mock_scenario": "verification-second-fail",
                    "change": "mock-change",
                },
                id="s6-wf-fail-stop",
                task_queue=TEMPORAL_TASK_QUEUE,
            )
    assert result["status"] in (TERMINAL_FAIL, "VERIFICATION_CHECK_FAILED")


def test_archive_gate_not_merged_into_terminal_classifier() -> None:
    """Archive gate is separate; Temporal terminal classifier does not invoke it."""
    import inspect

    import verification_engine as ve

    src = inspect.getsource(ve.classify_next_check)
    assert "archive_gate" not in src
    assert "run_archive_verify_gate" not in src
