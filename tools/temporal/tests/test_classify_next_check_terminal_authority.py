# -*- coding: utf-8 -*-
"""P0-A: Temporal terminal authority — exact plan/ledger/result coverage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import _fingerprint_subset, compute_plan_fingerprint  # noqa: E402

from verification_engine import (
    TERMINAL_FAIL,
    TERMINAL_SUCCESS,
    bounded_terminal_success_authorized,
    classify_next_check,
    classify_after_executor,
    ledger_path,
    plan_path,
    bounded_verify_dir,
)


def _plan(
    tmp: Path,
    change: str,
    checks: list[dict],
    fp: str | None = None,
    **extra,
) -> str:
    doc = {
        "schema_version": "bounded-verify-plan-v1",
        "change_id": change,
        "plan_id": "plan-1",
        "plan_status": "FROZEN",
        "plan_fingerprint": "",
        "required_checks": checks,
        "implementation_snapshot": {"content_digest": "b" * 64},
        **extra,
    }
    canonical_fp = compute_plan_fingerprint(_fingerprint_subset(doc))
    doc["plan_fingerprint"] = fp if fp is not None else canonical_fp
    dest = plan_path(tmp, change)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc), encoding="utf-8")
    return str(doc["plan_fingerprint"])


def _ledger(
    tmp: Path,
    change: str,
    *,
    entries: list,
    status: str = "ACTIVE",
    attestation: str | None = None,
    fp: str = "a" * 64,
) -> None:
    doc = {
        "schema_version": "bounded-verify-ledger-v1",
        "ledger_id": "led-1",
        "plan_id": "plan-1",
        "plan_fingerprint": fp,
        "ledger_revision": 3,
        "ledger_status": status,
        "entries": entries,
        "current_assignment": None,
    }
    if attestation:
        doc["completion_attestation"] = attestation
    ledger_path(tmp, change).parent.mkdir(parents=True, exist_ok=True)
    ledger_path(tmp, change).write_text(json.dumps(doc), encoding="utf-8")


def _pass_result(
    tmp: Path,
    change: str,
    *,
    check_id: str,
    fp: str,
    execution_id: str = "exec-1",
    rev: int = 1,
    epoch: int = 1,
) -> str:
    from hashlib import sha256

    rid = sha256(f"{fp}|{rev}|{check_id}|{execution_id}".encode()).hexdigest()
    doc = {
        "schema_version": "bounded-verify-result-v1",
        "result_id": rid,
        "plan_fingerprint": fp,
        "ledger_revision": rev,
        "assignment_epoch": epoch,
        "check_id": check_id,
        "execution_id": execution_id,
        "result_status": "PASS",
        "producer": "bounded_verify_executor",
    }
    rdir = bounded_verify_dir(tmp, change) / "results"
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir / f"{rid}.json").write_text(json.dumps(doc), encoding="utf-8")
    return rid


def _classify(tmp: Path, change: str) -> str:
    return classify_next_check(
        repo=tmp, change=change, raw_output="", completed=True
    ).status


def test_extra_pass_entry_blocked(tmp_path: Path) -> None:
    change = "extra-pass"
    checks = [{"check_id": "pyright-strict", "order": 1}]
    _plan(tmp_path, change, checks)
    _ledger(
        tmp_path,
        change,
        entries=[
            {"check_id": "pyright-strict", "order": 1, "state": "PASS"},
            {"check_id": "mypy", "order": 2, "state": "PASS"},
        ],
        status="COMPLETE",
        attestation="LEGACY_BOUNDED_VERIFICATION_COMPLETE",
    )
    assert _classify(tmp_path, change) != TERMINAL_SUCCESS


def test_missing_entry_blocked(tmp_path: Path) -> None:
    change = "missing-entry"
    _plan(
        tmp_path,
        change,
        [
            {"check_id": "pyright-strict", "order": 1},
            {"check_id": "mypy", "order": 2},
        ],
    )
    _ledger(
        tmp_path,
        change,
        entries=[{"check_id": "pyright-strict", "order": 1, "state": "PASS"}],
        status="COMPLETE",
    )
    assert _classify(tmp_path, change) == "BLOCKED"


def test_reordered_entry_blocked(tmp_path: Path) -> None:
    change = "reorder"
    _plan(
        tmp_path,
        change,
        [
            {"check_id": "pyright-strict", "order": 1},
            {"check_id": "mypy", "order": 2},
        ],
    )
    _ledger(
        tmp_path,
        change,
        entries=[
            {"check_id": "mypy", "order": 1, "state": "PASS"},
            {"check_id": "pyright-strict", "order": 2, "state": "PASS"},
        ],
        status="COMPLETE",
    )
    assert _classify(tmp_path, change) == "BLOCKED"


def test_pass_without_result_receipt_blocked(tmp_path: Path) -> None:
    change = "no-receipt"
    _plan(tmp_path, change, [{"check_id": "pyright-strict", "order": 1}])
    _ledger(
        tmp_path,
        change,
        entries=[{"check_id": "pyright-strict", "order": 1, "state": "PASS"}],
        status="COMPLETE",
        attestation="LEGACY_BOUNDED_VERIFICATION_COMPLETE",
    )
    assert _classify(tmp_path, change) == "BLOCKED"


def test_genuine_bounded_completion(tmp_path: Path) -> None:
    change = "genuine"
    sys.path.insert(0, str(BV_ROOT.parent / "bounded-verify-tests"))
    from test_impl_seed import seed_hermetic_impl_ok

    cdir = tmp_path / "openspec/changes" / change
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    seed_hermetic_impl_ok(change, root=tmp_path)
    from bounded_verify_impl_completion import implementation_completion_digest

    digest = implementation_completion_digest(change, root=tmp_path)
    checks = [
        {"check_id": "pyright-strict", "order": 1},
        {"check_id": "mypy", "order": 2},
    ]
    fp = _plan(
        tmp_path,
        change,
        checks,
        implementation_snapshot={"content_digest": digest},
    )
    r1 = _pass_result(
        tmp_path,
        change,
        check_id="pyright-strict",
        fp=fp,
        execution_id="e1",
        rev=1,
        epoch=1,
    )
    r2 = _pass_result(
        tmp_path,
        change,
        check_id="mypy",
        fp=fp,
        execution_id="e2",
        rev=2,
        epoch=2,
    )
    _ledger(
        tmp_path,
        change,
        entries=[
            {
                "check_id": "pyright-strict",
                "order": 1,
                "state": "PASS",
                "accepted_result_id": r1,
                "accepted_execution_id": "e1",
                "accepted_assignment_epoch": 1,
                "accepted_ledger_revision": 1,
            },
            {
                "check_id": "mypy",
                "order": 2,
                "state": "PASS",
                "accepted_result_id": r2,
                "accepted_execution_id": "e2",
                "accepted_assignment_epoch": 2,
                "accepted_ledger_revision": 2,
            },
        ],
        status="COMPLETE",
        attestation="LEGACY_BOUNDED_VERIFICATION_COMPLETE",
        fp=fp,
    )
    result = classify_next_check(
        repo=tmp_path, change=change, raw_output="", completed=True
    )
    assert result.status == TERMINAL_SUCCESS
    terminal = classify_after_executor(next_check=result)
    assert terminal is not None
    assert terminal["status"] == TERMINAL_SUCCESS


def test_no_ledger_agent_success_blocked(tmp_path: Path) -> None:
    result = classify_next_check(
        repo=tmp_path,
        change="no-ledger",
        raw_output="STATUS: ALL_REQUIRED_CHECKS_PASS\nPROGRESS: 5/5",
        completed=True,
    )
    assert result.status == "BLOCKED"


def test_duplicate_ledger_check_id_blocked(tmp_path: Path) -> None:
    change = "dup-id"
    _plan(
        tmp_path,
        change,
        [{"check_id": "pyright-strict", "order": 1}],
    )
    _ledger(
        tmp_path,
        change,
        entries=[
            {"check_id": "pyright-strict", "order": 1, "state": "PASS"},
            {"check_id": "pyright-strict", "order": 2, "state": "PASS"},
        ],
        status="COMPLETE",
    )
    assert _classify(tmp_path, change) == "BLOCKED"


def test_forged_complete_with_fail_row_blocked(tmp_path: Path) -> None:
    change = "forged-complete"
    fp = "c" * 64
    _plan(tmp_path, change, [{"check_id": "pyright-strict", "order": 1}], fp=fp)
    _ledger(
        tmp_path,
        change,
        entries=[{"check_id": "pyright-strict", "order": 1, "state": "FAIL"}],
        status="COMPLETE",
        fp=fp,
    )
    assert _classify(tmp_path, change) == TERMINAL_FAIL


def test_wrong_fingerprint_blocked(tmp_path: Path) -> None:
    change = "fp-mismatch"
    checks = [
        {"check_id": "pyright-strict", "order": 1},
        {"check_id": "mypy", "order": 2},
    ]
    fp = _plan(tmp_path, change, checks)
    _ledger(
        tmp_path,
        change,
        entries=[
            {"check_id": "pyright-strict", "order": 1, "state": "PASS"},
            {"check_id": "mypy", "order": 2, "state": "PASS"},
        ],
        status="COMPLETE",
        fp="b" * 64,
    )
    from bounded_verify_plan import read_plan

    reason = bounded_terminal_success_authorized(
        repo=tmp_path,
        change=change,
        ledger=json.loads(ledger_path(tmp_path, change).read_text()),
        plan=read_plan(change, root=tmp_path),
    )
    assert reason == "plan_fingerprint_mismatch"
