# -*- coding: utf-8 -*-
"""S6: bounded-verify ledger state machine + verification_engine classification."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
FIXTURES = BV_ROOT.parent / "bounded-verify-tests/fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_ledger import read_ledger, schedule_write  # noqa: E402
from bounded_verify_plan import draft_plan, freeze_plan, read_plan  # noqa: E402
from bounded_verify_plan_profile import (  # noqa: E402
    BOUNDED_STATIC_VERIFICATION_COMPLETE,
    FULL_REQUIRED_VERIFICATION_COMPLETE,
)
from bounded_verify_executor import (  # noqa: E402
    ASSIGNMENT_MISMATCH,
    BoundedVerifyExecutorError,
    run_current_check,
)
from verification_engine import (  # noqa: E402
    TERMINAL_FAIL,
    TERMINAL_SUCCESS,
    classify_next_check,
    classify_after_executor,
)


def _pass_runner(check_id: str, _reg: Path, _root: Path):
    return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS", "EXIT_CODE": 0}


def _fail_runner(check_id: str, _reg: Path, _root: Path):
    return "FAIL", {"CHECK_ID": check_id, "STATUS": "FAIL", "EXIT_CODE": 1}


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


def _freeze_two_check_plan(root: Path, change_id: str) -> dict:
    reg = FIXTURES / "registry_snapshot.yaml"
    checks = [
        {"check_id": "pyright-strict", "order": 1},
        {"check_id": "mypy", "order": 2},
    ]
    draft_plan(change_id, checks, root=root, registry_path=reg)
    freeze_plan(change_id, root=root, registry_path=reg)
    return read_plan(change_id, root=root)


def test_classify_blocks_agent_success_without_ledger(tmp_path: Path) -> None:
    result = classify_next_check(
        repo=tmp_path,
        change="missing",
        raw_output="STATUS: ALL_REQUIRED_CHECKS_PASS\nPROGRESS: 3/3",
        completed=True,
    )
    assert result.status == "BLOCKED"
    terminal = classify_after_executor(next_check=result)
    assert terminal is not None
    assert terminal["status"] == "BLOCKED"


def test_schedule_execute_drain_all_pass(tmp_path: Path) -> None:
    change = "sm-pass"
    _seed(tmp_path, change)
    plan = _freeze_two_check_plan(tmp_path, change)
    reg = FIXTURES / "registry_snapshot.yaml"
    for _ in range(2):
        hop = schedule_write(change, root=tmp_path)
        assert hop.check_required
        run_current_check(change, root=tmp_path, registry_path=reg, check_runner=_pass_runner)
        schedule_write(change, root=tmp_path)
    ledger = read_ledger(change, root=tmp_path)
    assert ledger["ledger_status"] == "COMPLETE"
    nc = classify_next_check(
        repo=tmp_path,
        change=change,
        raw_output="STATUS: ALL_REQUIRED_CHECKS_PASS",
        completed=True,
    )
    assert nc.status == TERMINAL_SUCCESS


def test_stale_assignment_epoch_rejected(tmp_path: Path) -> None:
    change = "stale-epoch"
    _seed(tmp_path, change)
    _freeze_two_check_plan(tmp_path, change)
    reg = FIXTURES / "registry_snapshot.yaml"
    schedule_write(change, root=tmp_path)
    with pytest.raises(BoundedVerifyExecutorError) as exc:
        run_current_check(
            change,
            root=tmp_path,
            registry_path=reg,
            check_runner=_pass_runner,
            assignment_epoch=999,
        )
    assert exc.value.code == ASSIGNMENT_MISMATCH


def test_fail_stop_not_complete(tmp_path: Path) -> None:
    change = "sm-fail"
    _seed(tmp_path, change)
    _freeze_two_check_plan(tmp_path, change)
    reg = FIXTURES / "registry_snapshot.yaml"
    schedule_write(change, root=tmp_path)
    run_current_check(change, root=tmp_path, registry_path=reg, check_runner=_fail_runner)
    hop = schedule_write(change, root=tmp_path)
    ledger = read_ledger(change, root=tmp_path)
    assert ledger["entries"][0]["state"] == "FAIL"
    assert ledger["ledger_status"] != "COMPLETE"
    nc = classify_next_check(
        repo=tmp_path,
        change=change,
        raw_output="",
        completed=True,
    )
    assert nc.status == TERMINAL_FAIL


def test_static_v2_plan_never_full_attestation(tmp_path: Path) -> None:
    change = "sm-static-v2"
    _seed(tmp_path, change)
    from verification_plan_compose import compose_verification_plan

    compose_verification_plan(change, "static_fast", root=tmp_path)
    from bounded_verify_plan import DEFAULT_REGISTRY_PATH

    freeze_plan(change, root=tmp_path, registry_path=DEFAULT_REGISTRY_PATH)
    plan = read_plan(change, root=tmp_path)
    reg = FIXTURES / "registry_snapshot.yaml"
    checks = list(plan["required_checks"])
    for _ in checks:
        schedule_write(change, root=tmp_path)
        run_current_check(change, root=tmp_path, registry_path=reg, check_runner=_pass_runner)
        schedule_write(change, root=tmp_path)
    ledger = read_ledger(change, root=tmp_path)
    assert ledger.get("completion_attestation") == BOUNDED_STATIC_VERIFICATION_COMPLETE
    assert ledger.get("completion_attestation") != FULL_REQUIRED_VERIFICATION_COMPLETE


@pytest.mark.asyncio
async def test_verification_execution_workflow_mock_all_pass() -> None:
    from temporalio.testing import WorkflowEnvironment
    from temporalio.worker import Worker

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
        ActivityConfig(mode="mock", repo_path=str(REPO_ROOT), mock_scenario="verification-all-pass")
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
                id="bv-mock-matrix-pass",
                task_queue=TEMPORAL_TASK_QUEUE,
            )
    assert result["status"] in (TERMINAL_SUCCESS, "ALL_REQUIRED_CHECKS_PASS")
