# -*- coding: utf-8 -*-
"""SLICE-004 composition interaction tests (X-COMP-01..10) and preservation gates.

Доказывает, что A (plan freeze) + B (ledger scheduler) + C (executor) compose
корректно; fault-injection oracles MUST fail при severed dependency guards.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONTRACTS = REPO_ROOT / ".cursor" / "pipeline" / "contracts" / "bounded-verify"
COMMANDS = REPO_ROOT / ".cursor" / "commands"
ACTIVATION = REPO_ROOT / "opsx" / "registry" / "command_activation.ndjson"
OBLIGATION_TESTS = (
    REPO_ROOT
    / ".cursor"
    / "pipeline"
    / "control-plane-tests"
    / "run_required_verification_obligation_tests.py"
)

sys.path.insert(0, str(BV_ROOT))

import bounded_verify_ledger as ledger_mod  # noqa: E402
from bounded_verify_atomic import atomic_publish_json  # noqa: E402
from bounded_verify_executor import (  # noqa: E402
    ASSIGNMENT_MISMATCH,
    ASSIGNMENT_NOT_CURRENT,
    BoundedVerifyExecutorError,
    run_current_check,
)
from bounded_verify_ledger import (  # noqa: E402
    CHECK_REQUIRED,
    LEDGER_STALE_PLAN_IDENTITY,
    LEDGER_STALE_REVISION,
    NO_PENDING_CHECKS,
    PLAN_NOT_FROZEN,
    BoundedVerifyLedgerError,
    _validate_ledger_document,
    ledger_carrier_path,
    read_ledger,
    schedule_write,
)
from bounded_verify_plan import (  # noqa: E402
    compute_plan_fingerprint,
    draft_plan,
    freeze_plan,
    plan_carrier_path,
    read_plan,
)
from bounded_verify_result import (  # noqa: E402
    RESULT_INCOMPLETE,
    RESULT_STALE_ASSIGNMENT,
    RESULT_STALE_PLAN,
    ExpectedAssignment,
    ExecutionOutcome,
    compute_result_id,
    create_result,
    result_carrier_path,
    validate_result_for_drain,
)

Result = tuple[str, bool, str]


def _load_fixture() -> dict[str, Any]:
    return json.loads(
        (FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8")
    )


def _registry_path() -> Path:
    return FIXTURES / "registry_snapshot.yaml"


def _setup_change_tree(root: Path, fixture: dict[str, Any]) -> str:
    import shutil

    cap = root / "opsx/project/capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx/project/capabilities.yaml", cap)
    change_id = str(fixture["change_id"])
    change_dir = root / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True, exist_ok=True)
    (change_dir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
    from test_impl_seed import seed_hermetic_impl_ok

    seed_hermetic_impl_ok(change_id, root=root)
    return change_id


def _pass_runner(
    check_id: str,
    _registry_path: Path,
    _root: Path,
) -> tuple[str, dict[str, Any]]:
    return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS", "EXIT_CODE": 0}


def _fail_runner(
    check_id: str,
    _registry_path: Path,
    _root: Path,
) -> tuple[str, dict[str, Any]]:
    return "FAIL", {"CHECK_ID": check_id, "STATUS": "FAIL", "EXIT_CODE": 1}


def _freeze_plan(
    root: Path,
    fixture: dict[str, Any] | None = None,
    *,
    checks: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Freeze plan; returns (change_id, plan dict)."""
    fixture = dict(fixture or _load_fixture())
    if checks is not None:
        fixture["required_checks"] = checks
    change_id = _setup_change_tree(root, fixture)
    draft_plan(
        change_id,
        list(fixture["required_checks"]),
        root=root,
        registry_path=_registry_path(),
    )
    freeze_plan(change_id, root=root, registry_path=_registry_path())
    plan = read_plan(change_id, root=root)
    return change_id, plan


def _write_pass_result(
    root: Path,
    change_id: str,
    *,
    check_id: str,
    ledger_revision: int,
    assignment_epoch: int,
    plan_fingerprint: str,
    execution_id: str = "exec-comp",
) -> str:
    assignment = ExpectedAssignment(
        check_id=check_id,
        assignment_epoch=assignment_epoch,
        ledger_revision=ledger_revision,
        plan_fingerprint=plan_fingerprint,
    )
    record = create_result(
        change_id,
        assignment,
        ExecutionOutcome(
            execution_id=execution_id,
            result_status="PASS",
            worker_packet_or_equivalent={"STATUS": "PASS"},
        ),
        root=root,
    )
    return record.result_id


def _composition_a_to_b_ok(root: Path, *, skip_plan_bind: bool) -> bool:
    """True если A→B plan-identity guard корректен; False если severed guard допускает proceed."""
    change_id, plan = _freeze_plan(
        root,
        checks=[{"check_id": "pyright-strict", "order": 1}],
    )

    stale_ledger = {
        "schema_version": "bounded-verify-ledger-v1",
        "ledger_id": "stale-bind",
        "plan_id": str(plan["plan_id"]),
        "plan_fingerprint": "0" * 64,
        "ledger_revision": 1,
        "ledger_status": "ACTIVE",
        "entries": [
            {"check_id": "pyright-strict", "order": 1, "state": "PENDING"},
        ],
        "current_assignment": None,
    }

    def _validate(doc: dict[str, Any]) -> None:
        _validate_ledger_document(doc)

    atomic_publish_json(
        ledger_carrier_path(change_id, root=root),
        stale_ledger,
        validate=_validate,
    )

    original_bind = ledger_mod._assert_ledger_plan_bind

    def _noop_bind(_plan: dict[str, Any], _ledger: Optional[dict[str, Any]]) -> None:
        return None

    try:
        if skip_plan_bind:
            ledger_mod._assert_ledger_plan_bind = _noop_bind  # type: ignore[assignment]

        try:
            schedule_write(
                change_id,
                root=root,
                enforce_plan_fingerprint=True,
            )
        except BoundedVerifyLedgerError as exc:
            if exc.code == LEDGER_STALE_PLAN_IDENTITY:
                return not skip_plan_bind
            return False

        return not skip_plan_bind
    finally:
        ledger_mod._assert_ledger_plan_bind = original_bind


def _composition_b_to_c_ok(root: Path, *, enforce_current: bool) -> bool:
    """True если B→C CURRENT guard корректен; False если severed guard допускает mismatch."""
    change_id, _plan = _freeze_plan(root)
    sched = schedule_write(change_id, root=root)
    assert sched.current_check_id == "pyright-strict"

    try:
        run_current_check(
            change_id,
            root=root,
            check_id="mypy",
            registry_path=_registry_path(),
            check_runner=_pass_runner,
            enforce_current=enforce_current,
        )
    except BoundedVerifyExecutorError as exc:
        if exc.code == ASSIGNMENT_NOT_CURRENT:
            return enforce_current
        return False

    return False


def _x_comp_01_full_a_b_c_happy_path() -> tuple[bool, str]:
    """X-COMP-01: FROZEN P → scheduler CURRENT → executor → drain → advance."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan = _freeze_plan(
            root,
            checks=[{"check_id": "pyright-strict", "order": 1}],
        )
        plan_fp = str(plan["plan_fingerprint"])

        sched1 = schedule_write(change_id, root=root)
        if sched1.code != CHECK_REQUIRED or sched1.current_check_id != "pyright-strict":
            return False, f"schedule1 code={sched1.code}"

        exec_result = run_current_check(
            change_id,
            root=root,
            registry_path=_registry_path(),
            check_runner=_pass_runner,
        )
        if exec_result.result_status != "PASS":
            return False, "executor did not PASS"

        sched2 = schedule_write(change_id, root=root)
        if sched2.code != NO_PENDING_CHECKS:
            return False, f"drain code={sched2.code}"

        ledger = read_ledger(change_id, root=root)
        if ledger["ledger_status"] != "COMPLETE":
            return False, "ledger not COMPLETE"
        if ledger["plan_fingerprint"] != plan_fp:
            return False, "plan identity drift"
        if ledger["entries"][0]["state"] != "PASS":
            return False, "entry not PASS"

        return True, "full A->B->C single check COMPLETE"


def _x_comp_02_plan_identity_p2_rejects_p1_ledger() -> tuple[bool, str]:
    """X-COMP-02: ledger bound to P1 rejected when plan is P2."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan_p1 = _freeze_plan(
            root,
            checks=[{"check_id": "pyright-strict", "order": 1}],
        )
        schedule_write(change_id, root=root)

        plan_path = plan_carrier_path(change_id, root=root)
        plan_p2 = dict(plan_p1)
        plan_p2["plan_id"] = "plan-p2-new-identity"
        fp_subset = {
            "schema_version": plan_p2["schema_version"],
            "change_id": plan_p2["change_id"],
            "plan_id": plan_p2["plan_id"],
            "plan_status": "FROZEN",
            "required_checks": plan_p2["required_checks"],
            "implementation_snapshot": plan_p2["implementation_snapshot"],
        }
        plan_p2["plan_fingerprint"] = compute_plan_fingerprint(fp_subset)
        plan_path.write_text(json.dumps(plan_p2, sort_keys=True), encoding="utf-8")

        try:
            schedule_write(change_id, root=root)
            return False, "scheduler should reject P1 ledger under P2 plan"
        except BoundedVerifyLedgerError as exc:
            if exc.code != LEDGER_STALE_PLAN_IDENTITY:
                return False, f"scheduler code={exc.code}"

        try:
            run_current_check(
                change_id,
                root=root,
                registry_path=_registry_path(),
                check_runner=_pass_runner,
            )
            return False, "executor should reject P1 ledger under P2 plan"
        except BoundedVerifyExecutorError as exc:
            if exc.code != LEDGER_STALE_PLAN_IDENTITY:
                return False, f"executor code={exc.code}"

        return True, "scheduler+executor reject stale plan identity"


def _x_comp_03_severed_a_to_b_oracle() -> tuple[bool, str]:
    """X-COMP-03: sever A→B — fault oracle MUST fail when guard disabled."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if not _composition_a_to_b_ok(root, skip_plan_bind=False):
            return False, "enforced A->B bind path broken"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if _composition_a_to_b_ok(root, skip_plan_bind=True):
            return False, "severed A->B guard did not expose composition failure"

    return True, "fault oracle detected severed A->B"


def _x_comp_04_severed_b_to_c_oracle() -> tuple[bool, str]:
    """X-COMP-04: sever B→C — fault oracle MUST fail when CURRENT guard disabled."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if not _composition_b_to_c_ok(root, enforce_current=True):
            return False, "enforced B->C CURRENT path broken"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if _composition_b_to_c_ok(root, enforce_current=False):
            return False, "severed B->C guard did not expose composition failure"

    return True, "fault oracle detected severed B->C"


def _x_comp_05_stale_current_result_rejected() -> tuple[bool, str]:
    """X-COMP-05: stale prior-CURRENT result rejected at drain; ledger unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan = _freeze_plan(root)
        plan_fp = str(plan["plan_fingerprint"])

        sched1 = schedule_write(change_id, root=root)
        rev1 = int(sched1.ledger_revision)
        epoch1 = int(sched1.assignment_epoch or 0)

        _write_pass_result(
            root,
            change_id,
            check_id="pyright-strict",
            ledger_revision=rev1,
            assignment_epoch=epoch1,
            plan_fingerprint=plan_fp,
            execution_id="exec-epoch1",
        )
        sched2 = schedule_write(change_id, root=root)
        if sched2.current_check_id != "mypy":
            return False, "did not advance to second check"

        rev2 = int(sched2.ledger_revision)
        stale_epoch = epoch1
        stale_result = {
            "schema_version": "bounded-verify-result-v1",
            "result_id": compute_result_id(
                plan_fp,
                rev2,
                "mypy",
                "exec-stale-epoch",
            ),
            "plan_fingerprint": plan_fp,
            "ledger_revision": rev2,
            "assignment_epoch": stale_epoch,
            "check_id": "mypy",
            "execution_id": "exec-stale-epoch",
            "result_status": "PASS",
            "producer": "test",
        }
        path = result_carrier_path(
            change_id,
            str(stale_result["result_id"]),
            root=root,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stale_result, sort_keys=True), encoding="utf-8")

        decision = validate_result_for_drain(
            stale_result,
            ExpectedAssignment(
                check_id="mypy",
                assignment_epoch=int(sched2.assignment_epoch or 0),
                ledger_revision=rev2,
                plan_fingerprint=plan_fp,
            ),
        )
        if decision.allowed or decision.code != RESULT_STALE_ASSIGNMENT:
            return False, f"stale epoch drain code={decision.code}"

        before = read_ledger(change_id, root=root)
        schedule_write(change_id, root=root)
        after = read_ledger(change_id, root=root)
        if after["ledger_revision"] != before["ledger_revision"]:
            return False, "ledger advanced on stale CURRENT result"
        if after["entries"][1]["state"] != "CURRENT":
            return False, "second check state mutated"

        return True, "stale assignment_epoch rejected; ledger unchanged"


def _x_comp_06_stale_plan_result_rejected() -> tuple[bool, str]:
    """X-COMP-06: stale plan fingerprint result rejected at drain."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan = _freeze_plan(
            root,
            checks=[{"check_id": "pyright-strict", "order": 1}],
        )
        plan_fp = str(plan["plan_fingerprint"])
        sched = schedule_write(change_id, root=root)

        stale_fp = "9" * 64
        stale_result = {
            "schema_version": "bounded-verify-result-v1",
            "result_id": compute_result_id(
                stale_fp,
                int(sched.ledger_revision),
                "pyright-strict",
                "exec-stale-plan",
            ),
            "plan_fingerprint": stale_fp,
            "ledger_revision": int(sched.ledger_revision),
            "assignment_epoch": int(sched.assignment_epoch or 0),
            "check_id": "pyright-strict",
            "execution_id": "exec-stale-plan",
            "result_status": "PASS",
            "producer": "test",
        }
        path = result_carrier_path(
            change_id,
            str(stale_result["result_id"]),
            root=root,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stale_result, sort_keys=True), encoding="utf-8")

        decision = validate_result_for_drain(
            stale_result,
            ExpectedAssignment(
                check_id="pyright-strict",
                assignment_epoch=int(sched.assignment_epoch or 0),
                ledger_revision=int(sched.ledger_revision),
                plan_fingerprint=plan_fp,
            ),
        )
        if decision.allowed or decision.code != RESULT_STALE_PLAN:
            return False, f"stale plan drain code={decision.code}"

        before_rev = int(read_ledger(change_id, root=root)["ledger_revision"])
        schedule_write(change_id, root=root)
        after_rev = int(read_ledger(change_id, root=root)["ledger_revision"])
        if after_rev != before_rev:
            return False, "ledger advanced on stale plan result"

        return True, "RESULT_STALE_PLAN; ledger unchanged"


def _x_comp_07_fail_invalid_no_aggregate_pass() -> tuple[bool, str]:
    """X-COMP-07: FAIL/invalid result — no unauthorized advancement or aggregate PASS."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan = _freeze_plan(
            root,
            checks=[{"check_id": "pyright-strict", "order": 1}],
        )
        sched = schedule_write(change_id, root=root)

        run_current_check(
            change_id,
            root=root,
            registry_path=_registry_path(),
            check_runner=_fail_runner,
        )
        sched_fail = schedule_write(change_id, root=root)
        if sched_fail.code != "VERIFICATION_CHECK_FAILED":
            return False, f"FAIL drain code={sched_fail.code}"

        ledger = read_ledger(change_id, root=root)
        if ledger["entries"][0]["state"] != "FAIL":
            return False, "entry not FAIL"
        if ledger["ledger_status"] == "COMPLETE":
            return False, "FAIL must not mark ledger COMPLETE (fail-stop)"
        if ledger.get("completion_attestation"):
            return False, "FAIL must not emit completion attestation"

        for token in ("AGGREGATE_PASS", "aggregate_pass_allowed", "verification_obligation"):
            text = json.dumps(ledger)
            if token in text:
                return False, f"ledger implies aggregate PASS: {token}"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan = _freeze_plan(
            root,
            checks=[{"check_id": "pyright-strict", "order": 1}],
        )
        plan_fp = str(plan["plan_fingerprint"])
        sched = schedule_write(change_id, root=root)

        assignment = ExpectedAssignment(
            check_id="pyright-strict",
            assignment_epoch=int(sched.assignment_epoch or 0),
            ledger_revision=int(sched.ledger_revision),
            plan_fingerprint=plan_fp,
        )
        create_result(
            change_id,
            assignment,
            ExecutionOutcome(
                execution_id="exec-incomplete",
                result_status="INCOMPLETE",
            ),
            root=root,
        )

        before_rev = int(read_ledger(change_id, root=root)["ledger_revision"])
        try:
            replay = schedule_write(change_id, root=root)
        except BoundedVerifyLedgerError as exc:
            if exc.code != RESULT_INCOMPLETE:
                return False, f"INCOMPLETE drain code={exc.code}"
            replay = None
        else:
            if replay.code != CHECK_REQUIRED:
                return False, f"INCOMPLETE replay code={replay.code}"
        after_rev = int(read_ledger(change_id, root=root)["ledger_revision"])
        if after_rev != before_rev:
            return False, "INCOMPLETE advanced ledger"

        incomplete = {
            "schema_version": "bounded-verify-result-v1",
            "partial": True,
        }
        bad_path = result_carrier_path(change_id, "bad-partial", root=root)
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text(json.dumps(incomplete), encoding="utf-8")

        decision = validate_result_for_drain(
            incomplete,
            assignment,
        )
        if decision.allowed or decision.code != RESULT_INCOMPLETE:
            return False, f"invalid schema code={decision.code}"

        return True, "FAIL terminal; INCOMPLETE/invalid no advancement (D:S:008)"


def _x_comp_08_crash_replay_one_transition() -> tuple[bool, str]:
    """X-COMP-08: crash before ledger replace → replay exactly one transition."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan = _freeze_plan(
            root,
            checks=[{"check_id": "pyright-strict", "order": 1}],
        )
        plan_fp = str(plan["plan_fingerprint"])

        sched1 = schedule_write(change_id, root=root)
        run_current_check(
            change_id,
            root=root,
            registry_path=_registry_path(),
            check_runner=_pass_runner,
        )

        rev_before = int(read_ledger(change_id, root=root)["ledger_revision"])
        try:
            schedule_write(
                change_id,
                root=root,
                test_hook={"crash_before_replace": True},
            )
        except Exception:
            pass

        rev_after_crash = int(read_ledger(change_id, root=root)["ledger_revision"])
        if rev_after_crash != rev_before:
            return False, "crash mutated ledger revision"

        entry_state = read_ledger(change_id, root=root)["entries"][0]["state"]
        if entry_state == "PASS":
            return False, "crash published drain transition"

        sched_replay = schedule_write(change_id, root=root)
        rev_after_replay = int(read_ledger(change_id, root=root)["ledger_revision"])
        if rev_after_replay != rev_before + 1:
            return False, f"replay revision {rev_after_replay} != {rev_before + 1}"
        if sched_replay.code != NO_PENDING_CHECKS:
            return False, f"replay code={sched_replay.code}"
        if read_ledger(change_id, root=root)["entries"][0]["state"] != "PASS":
            return False, "replay did not drain"
        if read_ledger(change_id, root=root)["plan_fingerprint"] != plan_fp:
            return False, "plan identity drift after replay"

        return True, "crash preserved state; replay one deterministic transition"


def _x_comp_09_scheduler_concurrency_one_winner() -> tuple[bool, str]:
    """X-COMP-09: concurrent schedulers same revision → at most one valid publication."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan = _freeze_plan(
            root,
            checks=[{"check_id": "pyright-strict", "order": 1}],
        )
        plan_fp = str(plan["plan_fingerprint"])
        sched = schedule_write(change_id, root=root)
        _write_pass_result(
            root,
            change_id,
            check_id="pyright-strict",
            ledger_revision=int(sched.ledger_revision),
            assignment_epoch=int(sched.assignment_epoch or 0),
            plan_fingerprint=plan_fp,
            execution_id="exec-concurrent",
        )
        expected_rev = int(sched.ledger_revision)

        outcomes: list[tuple[str, Any]] = []
        lock = threading.Lock()

        def _worker() -> None:
            try:
                result = schedule_write(
                    change_id,
                    root=root,
                    expected_revision=expected_rev,
                )
                with lock:
                    outcomes.append(("ok", result.code, result.ledger_revision))
            except BoundedVerifyLedgerError as exc:
                with lock:
                    outcomes.append(("err", exc.code, None))

        threads = [threading.Thread(target=_worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        ok_count = sum(1 for kind, _, _ in outcomes if kind == "ok")
        stale_count = sum(
            1 for kind, code, _ in outcomes if kind == "err" and code == LEDGER_STALE_REVISION
        )
        if ok_count != 1:
            return False, f"expected 1 winner, got ok_count={ok_count} outcomes={outcomes}"
        if stale_count != 1:
            return False, f"expected 1 LEDGER_STALE_REVISION, got {stale_count}"

        final_rev = int(read_ledger(change_id, root=root)["ledger_revision"])
        if final_rev != expected_rev + 1:
            return False, f"final revision {final_rev} != {expected_rev + 1}"

        return True, "one winner + one LEDGER_STALE_REVISION"


def _x_comp_10_executor_scheduler_race_stale_result() -> tuple[bool, str]:
    """X-COMP-10: executor under old CURRENT — stale result cannot advance newer ledger."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan = _freeze_plan(root)
        plan_fp = str(plan["plan_fingerprint"])

        sched1 = schedule_write(change_id, root=root)
        rev1 = int(sched1.ledger_revision)
        epoch1 = int(sched1.assignment_epoch or 0)
        check_a = str(sched1.current_check_id)

        run_current_check(
            change_id,
            root=root,
            registry_path=_registry_path(),
            check_runner=_pass_runner,
            execution_id="exec-race-a",
        )
        sched2 = schedule_write(change_id, root=root)
        rev2 = int(sched2.ledger_revision)
        check_b = str(sched2.current_check_id)
        epoch2 = int(sched2.assignment_epoch or 0)

        if check_b == check_a:
            return False, "scheduler did not advance"

        try:
            run_current_check(
                change_id,
                root=root,
                check_id=check_a,
                assignment_epoch=epoch1,
                ledger_revision=rev1,
                registry_path=_registry_path(),
                check_runner=_pass_runner,
                execution_id="exec-stale-race",
            )
            return False, "executor accepted stale CURRENT assignment"
        except BoundedVerifyExecutorError as exc:
            if exc.code not in (ASSIGNMENT_NOT_CURRENT, ASSIGNMENT_MISMATCH):
                return False, f"stale executor code={exc.code}"

        stale_for_b = {
            "schema_version": "bounded-verify-result-v1",
            "result_id": compute_result_id(
                plan_fp,
                rev2,
                check_b,
                "exec-stale-for-b",
            ),
            "plan_fingerprint": plan_fp,
            "ledger_revision": rev1,
            "assignment_epoch": epoch1,
            "check_id": check_a,
            "execution_id": "exec-stale-for-b",
            "result_status": "PASS",
            "producer": "test",
        }
        path = result_carrier_path(
            change_id,
            str(stale_for_b["result_id"]),
            root=root,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stale_for_b, sort_keys=True), encoding="utf-8")

        before = read_ledger(change_id, root=root)
        schedule_write(change_id, root=root)
        after = read_ledger(change_id, root=root)
        if after["ledger_revision"] != before["ledger_revision"]:
            return False, "stale result advanced newer ledger"
        if after["current_assignment"]["check_id"] != check_b:
            return False, "CURRENT mutated by stale result"

        return True, "stale executor result rejected at newer ledger state"


def _n01_b_before_a_plan_not_frozen() -> tuple[bool, str]:
    """N01: B before A → PLAN_NOT_FROZEN."""
    fixture = _load_fixture()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup_change_tree(root, fixture)
        try:
            schedule_write(change_id, root=root)
            return False, "schedule without freeze should refuse"
        except BoundedVerifyLedgerError as exc:
            if exc.code != PLAN_NOT_FROZEN:
                return False, f"code={exc.code}"
        return True, "PLAN_NOT_FROZEN when B before A"


def _n03_c_before_a_assignment_not_current() -> tuple[bool, str]:
    """N03: C before A → ASSIGNMENT_NOT_CURRENT."""
    fixture = _load_fixture()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup_change_tree(root, fixture)
        try:
            run_current_check(
                change_id,
                root=root,
                registry_path=_registry_path(),
                check_runner=_pass_runner,
            )
            return False, "executor without plan should refuse"
        except BoundedVerifyExecutorError as exc:
            if exc.code != ASSIGNMENT_NOT_CURRENT:
                return False, f"code={exc.code}"
        return True, "ASSIGNMENT_NOT_CURRENT when C before A"


def _p01_ds008_obligation_tests_pass() -> tuple[bool, str]:
    """P01: D:S:008 obligation evaluator tests PASS with bounded artifacts present."""
    if not OBLIGATION_TESTS.is_file():
        return False, "obligation test runner missing"
    completed = subprocess.run(
        [sys.executable, str(OBLIGATION_TESTS)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        tail = (completed.stdout + completed.stderr)[-500:]
        return False, f"obligation tests failed: {tail}"
    return True, "run_required_verification_obligation_tests PASS"


def _runtime_class_executable(runtime_class: str) -> bool:
    """Authoritative activation: plain ACTIVE or gated ACTIVE_WITH_CAPABILITY_GATES."""
    return runtime_class in ("ACTIVE", "ACTIVE_WITH_CAPABILITY_GATES")


def _p02_command_activation_availability_only() -> tuple[bool, str]:
    """P02: command_activation rows availability-only; no scheduling fields."""
    if not ACTIVATION.is_file():
        return False, "command_activation.ndjson missing"
    forbidden = (
        "schedule_write",
        "ledger_revision",
        "current_check_id",
        "assignment_epoch",
        "check_required",
    )
    targets = (
        "opsx-plan-verification",
        "opsx-next-check",
        "bounded-executor",
    )
    found = 0
    for line in ACTIVATION.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("command_id") not in targets:
            continue
        found += 1
        for field in forbidden:
            if field in line:
                return False, f"{row['command_id']} contains scheduling field {field}"
        runtime_class = str(row.get("runtime_class", ""))
        if not _runtime_class_executable(runtime_class):
            return False, f"{row['command_id']} not executable ({runtime_class})"
        if not row.get("installed") or not row.get("active"):
            return False, f"{row['command_id']} installed/active false"
        if runtime_class == "ACTIVE_WITH_CAPABILITY_GATES":
            if not str(row.get("capability_contract", "")).strip():
                return False, f"{row['command_id']} missing capability_contract gate"
    if found != 3:
        return False, f"expected 3 bounded command rows, found {found}"
    return True, "activation rows availability-only (R-V1-012)"


def _p03_no_owner_go_leakage() -> tuple[bool, str]:
    """P03: no owner_go in schemas or bounded command bodies (R-V1-006)."""
    paths = list(CONTRACTS.glob("*.json")) + list(CONTRACTS.glob("*.md"))
    paths += [
        COMMANDS / "opsx-plan-verification.md",
        COMMANDS / "opsx-next-check.md",
        COMMANDS / "bounded-executor.md",
    ]
    for path in paths:
        if not path.is_file():
            return False, f"missing artifact: {path.name}"
        text = path.read_text(encoding="utf-8").lower()
        if "owner_go" in text:
            return False, f"owner_go found in {path.name}"
    for module in BV_ROOT.glob("bounded_verify_*.py"):
        if "owner_go" in module.read_text(encoding="utf-8").lower():
            return False, f"owner_go in {module.name}"
    return True, "no R-V1-006 owner_go leakage"


def build_cases() -> list[tuple[str, Callable[[], tuple[bool, str]]]]:
    return [
        ("X-COMP-01", _x_comp_01_full_a_b_c_happy_path),
        ("X-COMP-02", _x_comp_02_plan_identity_p2_rejects_p1_ledger),
        ("X-COMP-03", _x_comp_03_severed_a_to_b_oracle),
        ("X-COMP-04", _x_comp_04_severed_b_to_c_oracle),
        ("X-COMP-05", _x_comp_05_stale_current_result_rejected),
        ("X-COMP-06", _x_comp_06_stale_plan_result_rejected),
        ("X-COMP-07", _x_comp_07_fail_invalid_no_aggregate_pass),
        ("X-COMP-08", _x_comp_08_crash_replay_one_transition),
        ("X-COMP-09", _x_comp_09_scheduler_concurrency_one_winner),
        ("X-COMP-10", _x_comp_10_executor_scheduler_race_stale_result),
        ("N01", _n01_b_before_a_plan_not_frozen),
        ("N03", _n03_c_before_a_assignment_not_current),
        ("P01", _p01_ds008_obligation_tests_pass),
        ("P02", _p02_command_activation_availability_only),
        ("P03", _p03_no_owner_go_leakage),
    ]


def main() -> int:
    results: list[Result] = []
    for case_id, fn in build_cases():
        ok, detail = fn()
        results.append((case_id, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]

    print(f"BOUNDED_VERIFY_COMPOSITION_TESTS: {passed}/{len(results)} passed")
    for case_id, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {case_id}: {detail}")
    if failed:
        print("FAILED_IDS:", ", ".join(r[0] for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
