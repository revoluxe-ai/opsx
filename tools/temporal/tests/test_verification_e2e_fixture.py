"""S6: isolated fixture flow compose → classify → fail/stale (mock execution)."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BV = REPO / ".cursor" / "pipeline" / "bounded-verify"
sys.path.insert(0, str(BV))

from bounded_verify_ledger import BoundedVerifyLedgerError, schedule_write  # noqa: E402
from bounded_verify_plan import BoundedVerifyPlanError, draft_plan, freeze_plan  # noqa: E402
from verification_engine import (  # noqa: E402
    TERMINAL_FAIL,
    TERMINAL_SUCCESS,
    classify_next_check,
)
from verification_plan_compose import compose_verification_plan  # noqa: E402


def _seed_change(root: Path, change_id: str) -> None:
    cap_src = REPO / "opsx" / "project" / "capabilities.yaml"
    cap_dst = root / "opsx" / "project" / "capabilities.yaml"
    cap_dst.parent.mkdir(parents=True, exist_ok=True)
    cap_dst.write_text(cap_src.read_text(encoding="utf-8"), encoding="utf-8")
    for rel in (
        ".cursor/static-check/registry.yaml",
        "openspec/policies/VERIFY_POLICY.md",
        ".cursor/pipeline/bounded-verify/verification_obligations.py",
    ):
        src = REPO / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    cdir = root / "openspec" / "changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")
    for rel in ("src/stroy", "tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO / ".cursor/pipeline/bounded-verify-tests"))
    from test_impl_seed import seed_hermetic_impl_ok

    seed_hermetic_impl_ok(change_id, root=root)


def test_full_required_compose_fail_closed_without_adapters(tmp_path: Path) -> None:
    change = "e2e-change"
    _seed_change(tmp_path, change)
    try:
        compose_verification_plan(change, "full_required", root=tmp_path)
    except BoundedVerifyPlanError as exc:
        assert exc.code == "PLAN_COVERAGE_INCOMPLETE"
    else:
        raise AssertionError("expected BoundedVerifyPlanError PLAN_COVERAGE_INCOMPLETE")


def test_compose_freeze_schedule_classify_pass(tmp_path: Path) -> None:
    change = "e2e-pass"
    _seed_change(tmp_path, change)
    compose_verification_plan(change, "bounded_minimal", root=tmp_path)
    freeze_plan(change, root=tmp_path)
    sched = schedule_write(change, root=tmp_path, write=True)
    assert sched.check_required is True
    result = classify_next_check(
        repo=tmp_path,
        change=change,
        raw_output="",
        completed=True,
    )
    assert result.status == "CHECK_REQUIRED"


def test_stale_tasks_digest_blocks_schedule(tmp_path: Path) -> None:
    change = "e2e-stale"
    _seed_change(tmp_path, change)
    compose_verification_plan(change, "bounded_minimal", root=tmp_path)
    freeze_plan(change, root=tmp_path)
    tasks = tmp_path / "openspec" / "changes" / change / "tasks.md"
    tasks.write_text("- [x] done\n- [ ] new work\n", encoding="utf-8")
    try:
        schedule_write(change, root=tmp_path, write=True)
        raise AssertionError("expected BLOCKED_SNAPSHOT_DRIFT")
    except BoundedVerifyLedgerError as exc:
        assert exc.code == "BLOCKED_SNAPSHOT_DRIFT"


def test_all_pass_classify_success(tmp_path: Path) -> None:
    from bounded_verify_executor import run_current_check

    change = "e2e-complete"
    _seed_change(tmp_path, change)
    compose_verification_plan(change, "bounded_minimal", root=tmp_path)
    freeze_plan(change, root=tmp_path)
    reg = REPO / ".cursor/static-check/registry.yaml"

    def _pass_runner(check_id: str, _reg: Path, _root: Path):
        return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS", "EXIT_CODE": 0}

    plan = json.loads(
        (tmp_path / "openspec/changes" / change / "bounded-verify/plan.json").read_text()
    )
    for _ in plan["required_checks"]:
        schedule_write(change, root=tmp_path, write=True)
        run_current_check(change, root=tmp_path, registry_path=reg, check_runner=_pass_runner)
        schedule_write(change, root=tmp_path, write=True)
    result = classify_next_check(
        repo=tmp_path,
        change=change,
        raw_output="",
        completed=True,
    )
    assert result.status == TERMINAL_SUCCESS
