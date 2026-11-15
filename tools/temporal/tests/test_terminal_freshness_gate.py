# -*- coding: utf-8 -*-
"""P0: TERMINAL_SUCCESS requires scheduler-equivalent input freshness."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
FIXTURES = BV_ROOT.parent / "bounded-verify-tests/fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_executor import (  # noqa: E402
    BoundedVerifyExecutorError,
    run_current_check,
)
from bounded_verify_ledger import read_ledger, schedule_write  # noqa: E402
from bounded_verify_plan import BoundedVerifyPlanError, freeze_plan  # noqa: E402
from bounded_verify_supersession import publish_successor_plan  # noqa: E402
from verification_engine import TERMINAL_SUCCESS, classify_next_check  # noqa: E402
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


def _complete_bounded_minimal(root: Path, change: str) -> None:
    compose_verification_plan(change, "bounded_minimal", root=root)
    freeze_plan(change, root=root)
    reg = FIXTURES / "registry_snapshot.yaml"
    plan = json.loads(
        (root / "openspec/changes" / change / "bounded-verify/plan.json").read_text()
    )
    for _ in plan["required_checks"]:
        schedule_write(change, root=root)
        run_current_check(change, root=root, registry_path=reg, check_runner=_pass_runner)
        schedule_write(change, root=root)
    assert read_ledger(change, root=root)["ledger_status"] == "COMPLETE"


def _classify(root: Path, change: str):
    return classify_next_check(repo=root, change=change, raw_output="", completed=True)


def test_terminal_success_when_inputs_unchanged(tmp_path: Path) -> None:
    change = "fresh-ok"
    _seed(tmp_path, change)
    _complete_bounded_minimal(tmp_path, change)
    r1 = _classify(tmp_path, change)
    r2 = _classify(tmp_path, change)
    assert r1.status == TERMINAL_SUCCESS
    assert r2.status == TERMINAL_SUCCESS
    assert r1.block_reason is None


def test_product_change_blocks_terminal(tmp_path: Path) -> None:
    change = "fresh-product"
    _seed(tmp_path, change)
    _complete_bounded_minimal(tmp_path, change)
    prod = tmp_path / "src/stroy"
    prod.mkdir(parents=True, exist_ok=True)
    (prod / "drift.py").write_text("x = 1\n", encoding="utf-8")
    result = _classify(tmp_path, change)
    assert result.status == "BLOCKED"
    assert result.block_reason == "BLOCKED_SNAPSHOT_DRIFT"


def test_test_file_change_blocks_terminal(tmp_path: Path) -> None:
    change = "fresh-tests"
    _seed(tmp_path, change)
    _complete_bounded_minimal(tmp_path, change)
    tests = tmp_path / "tests/unit"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_drift.py").write_text("def test_x(): pass\n", encoding="utf-8")
    result = _classify(tmp_path, change)
    assert result.status == "BLOCKED"
    assert result.block_reason == "BLOCKED_SNAPSHOT_DRIFT"


def test_policy_change_blocks_terminal(tmp_path: Path) -> None:
    change = "fresh-policy"
    _seed(tmp_path, change)
    _complete_bounded_minimal(tmp_path, change)
    policy = tmp_path / "openspec/policies/VERIFY_POLICY.md"
    policy.write_text(policy.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    result = _classify(tmp_path, change)
    assert result.status == "BLOCKED"
    assert result.block_reason == "BLOCKED_SNAPSHOT_DRIFT"


def test_scenario_plan_add_blocks_terminal(tmp_path: Path) -> None:
    change = "fresh-scenario"
    _seed(tmp_path, change)
    _complete_bounded_minimal(tmp_path, change)
    scen = tmp_path / "openspec/changes" / change / "scenario-coverage"
    scen.mkdir(parents=True)
    (scen / "plan.md").write_text(
        "### 1. `Scenario: new-case`\n", encoding="utf-8"
    )
    result = _classify(tmp_path, change)
    assert result.status == "BLOCKED"
    assert result.block_reason in (
        "BLOCKED_CONDITIONAL_APPLICABILITY_DRIFT",
        "BLOCKED_SNAPSHOT_DRIFT",
    )


def test_performance_trigger_blocks_terminal(tmp_path: Path) -> None:
    change = "fresh-perf"
    _seed(tmp_path, change)
    _complete_bounded_minimal(tmp_path, change)
    perf = tmp_path / "openspec/changes" / change / "performance-review"
    perf.mkdir(parents=True)
    (perf / "TRIGGER.json").write_text('{"triggered": true}\n', encoding="utf-8")
    result = _classify(tmp_path, change)
    assert result.status == "BLOCKED"
    assert result.block_reason in (
        "BLOCKED_CONDITIONAL_APPLICABILITY_DRIFT",
        "BLOCKED_SNAPSHOT_DRIFT",
    )


def test_supersession_with_active_assignment_blocked(tmp_path: Path) -> None:
    change = "fresh-super-active"
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
