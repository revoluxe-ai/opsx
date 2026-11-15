"""Integration: Temporal verification_engine reads TARGET bounded-verify carriers."""

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
    classify_next_check,
    ledger_path,
    plan_path,
    validate_assignment_for_execute,
    build_execute_prompt,
)


def _write_plan(tmp_path: Path, change: str, *, fingerprint: str | None = None) -> str:
    plan = {
        "schema_version": "bounded-verify-plan-v1",
        "change_id": change,
        "plan_id": "plan-1",
        "plan_status": "FROZEN",
        "plan_fingerprint": "",
        "required_checks": [
            {"check_id": "pyright-strict", "order": 1},
            {"check_id": "mypy", "order": 2},
        ],
        "implementation_snapshot": {"content_digest": "b" * 64},
    }
    canonical = compute_plan_fingerprint(_fingerprint_subset(plan))
    plan["plan_fingerprint"] = fingerprint if fingerprint is not None else canonical
    dest = plan_path(tmp_path, change)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(plan), encoding="utf-8")
    return str(plan["plan_fingerprint"])


def _write_ledger(
    tmp_path: Path,
    change: str,
    *,
    entries: list[dict],
    current_assignment: dict | None,
    status: str = "ACTIVE",
    plan_fingerprint: str = "a" * 64,
) -> None:
    ledger = {
        "schema_version": "bounded-verify-ledger-v1",
        "ledger_id": "led-1",
        "plan_id": "plan-1",
        "plan_fingerprint": plan_fingerprint,
        "ledger_revision": 1,
        "ledger_status": status,
        "entries": entries,
        "current_assignment": current_assignment,
    }
    dest = ledger_path(tmp_path, change)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(ledger), encoding="utf-8")


def test_classify_check_required_builds_bounded_executor_prompt(tmp_path: Path) -> None:
    change = "fixture-change"
    fp = _write_plan(tmp_path, change)
    _write_ledger(
        tmp_path,
        change,
        plan_fingerprint=fp,
        entries=[
            {"check_id": "pyright-strict", "order": 1, "state": "CURRENT"},
            {"check_id": "mypy", "order": 2, "state": "PENDING"},
        ],
        current_assignment={"check_id": "pyright-strict", "assignment_epoch": 1},
    )
    result = classify_next_check(
        repo=tmp_path,
        change=change,
        raw_output="CHECK_REQUIRED check_id=pyright-strict",
        completed=True,
    )
    assert result.status == "CHECK_REQUIRED"
    assert result.execute_assignment is not None
    assert result.execute_assignment.get("bounded_verify_target") is True
    assert validate_assignment_for_execute(result.execute_assignment) == []
    prompt = build_execute_prompt(result.execute_assignment)
    assert prompt.startswith("/bounded-executor")
    assert "mode=bounded" in prompt


def test_classify_fail_entry_is_terminal_fail(tmp_path: Path) -> None:
    change = "fixture-fail"
    fp = _write_plan(tmp_path, change)
    _write_ledger(
        tmp_path,
        change,
        plan_fingerprint=fp,
        entries=[
            {"check_id": "pyright-strict", "order": 1, "state": "FAIL"},
            {"check_id": "mypy", "order": 2, "state": "PENDING"},
        ],
        current_assignment=None,
    )
    result = classify_next_check(
        repo=tmp_path,
        change=change,
        raw_output="",
        completed=True,
    )
    assert result.status == TERMINAL_FAIL


def test_classify_complete_all_pass(tmp_path: Path) -> None:
    from hashlib import sha256
    from verification_engine import bounded_verify_dir

    change = "fixture-pass"
    fp = _write_plan(tmp_path, change)
    rdir = bounded_verify_dir(tmp_path, change) / "results"
    rdir.mkdir(parents=True, exist_ok=True)
    bindings: list[dict] = []
    for idx, cid in enumerate(("pyright-strict", "mypy"), start=1):
        eid = f"exec-{idx}"
        rev = idx
        rid = sha256(f"{fp}|{rev}|{cid}|{eid}".encode()).hexdigest()
        (rdir / f"{rid}.json").write_text(
            json.dumps(
                {
                    "schema_version": "bounded-verify-result-v1",
                    "result_id": rid,
                    "plan_fingerprint": fp,
                    "ledger_revision": rev,
                    "assignment_epoch": idx,
                    "check_id": cid,
                    "execution_id": eid,
                    "result_status": "PASS",
                    "producer": "bounded_verify_executor",
                }
            ),
            encoding="utf-8",
        )
        bindings.append(
            {
                "check_id": cid,
                "order": idx,
                "state": "PASS",
                "accepted_result_id": rid,
                "accepted_execution_id": eid,
                "accepted_assignment_epoch": idx,
                "accepted_ledger_revision": rev,
            }
        )
    _write_ledger(
        tmp_path,
        change,
        plan_fingerprint=fp,
        entries=bindings,
        current_assignment=None,
        status="COMPLETE",
    )
    ledger = json.loads(
        (bounded_verify_dir(tmp_path, change) / "ledger.json").read_text()
    )
    ledger["completion_attestation"] = "LEGACY_BOUNDED_VERIFICATION_COMPLETE"
    (bounded_verify_dir(tmp_path, change) / "ledger.json").write_text(
        json.dumps(ledger), encoding="utf-8"
    )
    _write_plan(tmp_path, change, fingerprint=fp)
    result = classify_next_check(
        repo=tmp_path,
        change=change,
        raw_output="",
        completed=True,
    )
    assert result.status == TERMINAL_SUCCESS
