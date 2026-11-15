# -*- coding: utf-8 -*-
"""P0: terminal success requires ledger-bound accepted_result_id chain."""

from __future__ import annotations

import json
import shutil
import sys
from hashlib import sha256
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
FIXTURES = BV_ROOT.parent / "bounded-verify-tests/fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_executor import run_current_check  # noqa: E402
from bounded_verify_ledger import read_ledger, schedule_write  # noqa: E402
from bounded_verify_plan import draft_plan, freeze_plan  # noqa: E402
from verification_engine import TERMINAL_SUCCESS, classify_next_check  # noqa: E402


def _pass_runner(check_id: str, _reg: Path, _root: Path):
    return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS", "EXIT_CODE": 0}


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


def _freeze_two(root: Path, change: str) -> None:
    reg = FIXTURES / "registry_snapshot.yaml"
    draft_plan(
        change,
        [
            {"check_id": "pyright-strict", "order": 1},
            {"check_id": "mypy", "order": 2},
        ],
        root=root,
        registry_path=reg,
    )
    freeze_plan(change, root=root, registry_path=reg)


def test_positive_scheduler_executor_drain_terminal(tmp_path: Path) -> None:
    """E2E: real writer + drain binds results; Temporal classifier accepts."""
    change = "bind-e2e"
    _seed(tmp_path, change)
    _freeze_two(tmp_path, change)
    reg = FIXTURES / "registry_snapshot.yaml"
    for _ in range(2):
        schedule_write(change, root=tmp_path)
        run_current_check(change, root=tmp_path, registry_path=reg, check_runner=_pass_runner)
        schedule_write(change, root=tmp_path)
    ledger = read_ledger(change, root=tmp_path)
    assert ledger["ledger_status"] == "COMPLETE"
    for entry in ledger["entries"]:
        assert entry["state"] == "PASS"
        assert entry.get("accepted_result_id")
    nc = classify_next_check(
        repo=tmp_path, change=change, raw_output="", completed=True
    )
    assert nc.status == TERMINAL_SUCCESS


def _write_orphan_pass(
    tmp: Path,
    change: str,
    *,
    fp: str,
    check_id: str,
    rev: int,
    epoch: int,
    eid: str,
) -> str:
    rid = sha256(f"{fp}|{rev}|{check_id}|{eid}".encode()).hexdigest()
    rdir = tmp / "openspec/changes" / change / "bounded-verify/results"
    rdir.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": "bounded-verify-result-v1",
        "result_id": rid,
        "plan_fingerprint": fp,
        "ledger_revision": rev,
        "assignment_epoch": epoch,
        "check_id": check_id,
        "execution_id": eid,
        "result_status": "PASS",
        "producer": "bounded_verify_executor",
    }
    (rdir / f"{rid}.json").write_text(json.dumps(doc), encoding="utf-8")
    return rid


def test_plausible_pass_without_ledger_binding_blocked(tmp_path: Path) -> None:
    from test_classify_next_check_terminal_authority import _classify, _ledger, _plan

    change = "orphan-pass"
    fp = _plan(tmp_path, change, [{"check_id": "pyright-strict", "order": 1}])
    _write_orphan_pass(
        tmp_path, change, fp=fp, check_id="pyright-strict", rev=1, epoch=1, eid="e1"
    )
    _ledger(
        tmp_path,
        change,
        entries=[{"check_id": "pyright-strict", "order": 1, "state": "PASS"}],
        status="COMPLETE",
        fp=fp,
        attestation="LEGACY_BOUNDED_VERIFICATION_COMPLETE",
    )
    assert _classify(tmp_path, change) == "BLOCKED"


def test_wrong_epoch_binding_blocked(tmp_path: Path) -> None:
    from test_classify_next_check_terminal_authority import _classify, _ledger, _plan

    change = "wrong-epoch"
    fp = _plan(tmp_path, change, [{"check_id": "pyright-strict", "order": 1}])
    rid = _write_orphan_pass(
        tmp_path, change, fp=fp, check_id="pyright-strict", rev=2, epoch=1, eid="e1"
    )
    _ledger(
        tmp_path,
        change,
        entries=[
            {
                "check_id": "pyright-strict",
                "order": 1,
                "state": "PASS",
                "accepted_result_id": rid,
                "accepted_execution_id": "e1",
                "accepted_assignment_epoch": 99,
                "accepted_ledger_revision": 2,
            }
        ],
        status="COMPLETE",
        fp=fp,
        attestation="LEGACY_BOUNDED_VERIFICATION_COMPLETE",
    )
    assert _classify(tmp_path, change) == "BLOCKED"


def test_tampered_frozen_fingerprint_blocked(tmp_path: Path) -> None:
    change = "tamper-fp"
    _seed(tmp_path, change)
    _freeze_two(tmp_path, change)
    plan_path = tmp_path / "openspec/changes" / change / "bounded-verify/plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["plan_fingerprint"] = "f" * 64
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    nc = classify_next_check(
        repo=tmp_path, change=change, raw_output="", completed=True
    )
    assert nc.status == "BLOCKED"
