# -*- coding: utf-8 -*-
"""Ledger scheduler authority для bounded verification (R-V1-005B / SLICE-002).

Единственный NORMAL EXECUTION writer ``ledger.json`` и CURRENT через
``schedule_write``. Не выполняет checks и не мутирует frozen plan.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Optional, Sequence

from bounded_verify_atomic import atomic_publish_json, read_canonical_json
from bounded_verify_plan import (
    PLAN_NOT_FOUND,
    BoundedVerifyPlanError,
    read_plan,
)
from bounded_verify_plan_digest import content_digest_required_for_drain
from bounded_verify_snapshot import assert_snapshot_fresh
from bounded_verify_result import (
    BoundedVerifyResultError,
    ExpectedAssignment,
    find_result_for_current,
    validate_result_for_drain,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LEDGER_SCHEMA_PATH = (
    REPO_ROOT
    / ".cursor"
    / "pipeline"
    / "contracts"
    / "bounded-verify"
    / "LEDGER_SCHEMA.json"
)

LEDGER_SCHEMA_VERSION = "bounded-verify-ledger-v1"

PLAN_NOT_FROZEN = "PLAN_NOT_FROZEN"
LEDGER_STALE_PLAN_IDENTITY = "LEDGER_STALE_PLAN_IDENTITY"
LEDGER_STALE_REVISION = "LEDGER_STALE_REVISION"
NO_PENDING_CHECKS = "NO_PENDING_CHECKS"
CHECK_NOT_IN_PLAN = "CHECK_NOT_IN_PLAN"
LEDGER_INVALID = "LEDGER_INVALID"
LEDGER_NOT_FOUND = "LEDGER_NOT_FOUND"
CHECK_REQUIRED = "CHECK_REQUIRED"
MULTIPLE_CURRENT = "MULTIPLE_CURRENT"


class BoundedVerifyLedgerError(Exception):
    """Ошибка bounded verification ledger scheduler."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ScheduleWriteResult:
    """Результат ``schedule_write`` hop."""

    change_id: str
    ledger_revision: int
    ledger_status: str
    current_check_id: Optional[str]
    assignment_epoch: Optional[int]
    check_required: bool
    code: str
    message: str = ""


@contextlib.contextmanager
def _scheduler_lock(lock_path: Path):
    """Advisory exclusive lock для schedule_write critical section."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            with contextlib.suppress(OSError):
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            with contextlib.suppress(OSError):
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def bounded_verify_dir(change_id: str, *, root: Path = REPO_ROOT) -> Path:
    """Runtime root bounded-verify для change."""
    return root / "openspec" / "changes" / change_id / "bounded-verify"


def ledger_carrier_path(change_id: str, *, root: Path = REPO_ROOT) -> Path:
    """Путь к runtime carrier ledger.json."""
    return bounded_verify_dir(change_id, root=root) / "ledger.json"


def scheduler_lock_path(change_id: str, *, root: Path = REPO_ROOT) -> Path:
    """Путь к .scheduler.lock."""
    return bounded_verify_dir(change_id, root=root) / ".scheduler.lock"


def _count_current_entries(entries: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for e in entries if e.get("state") == "CURRENT")


def _validate_ledger_document(doc: Mapping[str, Any]) -> None:
    """Минимальная schema validation по LEDGER_SCHEMA.json (B09)."""
    required = (
        "schema_version",
        "ledger_id",
        "plan_id",
        "plan_fingerprint",
        "ledger_revision",
        "ledger_status",
        "entries",
        "current_assignment",
    )
    for key in required:
        if key not in doc:
            raise BoundedVerifyLedgerError(LEDGER_INVALID, f"missing field: {key}")

    if doc["schema_version"] != LEDGER_SCHEMA_VERSION:
        raise BoundedVerifyLedgerError(LEDGER_INVALID, "invalid schema_version")

    status = str(doc["ledger_status"])
    if status not in ("ACTIVE", "COMPLETE"):
        raise BoundedVerifyLedgerError(LEDGER_INVALID, "invalid ledger_status")

    revision = doc["ledger_revision"]
    if not isinstance(revision, int) or revision < 1:
        raise BoundedVerifyLedgerError(LEDGER_INVALID, "invalid ledger_revision")

    entries = doc["entries"]
    if not isinstance(entries, list) or not entries:
        raise BoundedVerifyLedgerError(LEDGER_INVALID, "entries must be non-empty")

    current_count = _count_current_entries(entries)
    if current_count > 1:
        raise BoundedVerifyLedgerError(
            MULTIPLE_CURRENT,
            f"at most one CURRENT allowed, found {current_count}",
        )

    assignment = doc["current_assignment"]
    if status == "COMPLETE":
        if assignment is not None:
            raise BoundedVerifyLedgerError(
                LEDGER_INVALID,
                "COMPLETE ledger must have null current_assignment",
            )
        if current_count != 0:
            raise BoundedVerifyLedgerError(
                LEDGER_INVALID,
                "COMPLETE ledger must have no CURRENT entry",
            )
        for entry in entries:
            if entry.get("state") == "PASS" and not entry.get("accepted_result_id"):
                raise BoundedVerifyLedgerError(
                    LEDGER_INVALID,
                    "PASS entry missing accepted_result_id evidence binding",
                )
    elif current_count == 1:
        if not isinstance(assignment, dict):
            raise BoundedVerifyLedgerError(
                LEDGER_INVALID,
                "ACTIVE with CURRENT requires current_assignment",
            )
        current_id = next(
            e["check_id"] for e in entries if e.get("state") == "CURRENT"
        )
        if assignment.get("check_id") != current_id:
            raise BoundedVerifyLedgerError(
                LEDGER_INVALID,
                "current_assignment.check_id mismatch",
            )
    elif current_count == 0 and assignment is not None:
        raise BoundedVerifyLedgerError(
            LEDGER_INVALID,
            "current_assignment without CURRENT entry",
        )

    for item in entries:
        if not isinstance(item, dict):
            raise BoundedVerifyLedgerError(LEDGER_INVALID, "entry invalid")
        state = str(item.get("state", ""))
        if state not in ("PENDING", "CURRENT", "PASS", "FAIL"):
            raise BoundedVerifyLedgerError(LEDGER_INVALID, f"invalid state: {state}")


def _read_existing_ledger(
    change_id: str,
    *,
    root: Path,
) -> Optional[MutableMapping[str, Any]]:
    path = ledger_carrier_path(change_id, root=root)
    doc = read_canonical_json(path)
    if doc is None:
        return None
    _validate_ledger_document(doc)
    return doc


def read_ledger(change_id: str, *, root: Path = REPO_ROOT) -> dict[str, Any]:
    """Читает canonical ledger.json; отказывает если absent/invalid."""
    doc = _read_existing_ledger(change_id, root=root)
    if doc is None:
        raise BoundedVerifyLedgerError(LEDGER_NOT_FOUND, "ledger not found or invalid")
    return dict(doc)


def _require_frozen_plan(change_id: str, *, root: Path) -> dict[str, Any]:
    try:
        plan = read_plan(change_id, root=root)
    except BoundedVerifyPlanError as exc:
        if exc.code == PLAN_NOT_FOUND:
            raise BoundedVerifyLedgerError(
                PLAN_NOT_FROZEN,
                "plan not found",
            ) from exc
        raise BoundedVerifyLedgerError(exc.code, exc.message) from exc

    if plan.get("plan_status") != "FROZEN":
        raise BoundedVerifyLedgerError(PLAN_NOT_FROZEN, "plan is not FROZEN")
    return plan


def _plan_check_set(plan: Mapping[str, Any]) -> set[str]:
    return {str(item["check_id"]) for item in plan["required_checks"]}


def _ordered_plan_checks(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks = [dict(item) for item in plan["required_checks"]]
    checks.sort(key=lambda x: int(x["order"]))
    return checks


def _assert_ledger_plan_bind(
    plan: Mapping[str, Any],
    ledger: Optional[Mapping[str, Any]],
) -> None:
    if ledger is None:
        return
    if ledger.get("plan_fingerprint") != plan.get("plan_fingerprint"):
        raise BoundedVerifyLedgerError(
            LEDGER_STALE_PLAN_IDENTITY,
            "ledger plan_fingerprint mismatch",
        )
    if ledger.get("plan_id") != plan.get("plan_id"):
        raise BoundedVerifyLedgerError(
            LEDGER_STALE_PLAN_IDENTITY,
            "ledger plan_id mismatch",
        )


def _init_ledger_from_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Создаёт ledger с entries ⊆ plan.required_checks (B03)."""
    ordered = _ordered_plan_checks(plan)
    entries = [
        {
            "check_id": str(item["check_id"]),
            "order": int(item["order"]),
            "state": "PENDING",
        }
        for item in ordered
    ]
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "ledger_id": str(uuid.uuid4()),
        "plan_id": str(plan["plan_id"]),
        "plan_fingerprint": str(plan["plan_fingerprint"]),
        "ledger_revision": 1,
        "ledger_status": "ACTIVE",
        "entries": entries,
        "current_assignment": None,
    }


def _assert_entries_subset_of_plan(
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
) -> None:
    allowed = _plan_check_set(plan)
    for entry in ledger["entries"]:
        check_id = str(entry["check_id"])
        if check_id not in allowed:
            raise BoundedVerifyLedgerError(
                CHECK_NOT_IN_PLAN,
                f"check_id not in frozen plan: {check_id}",
            )


def _all_terminal(entries: Sequence[Mapping[str, Any]]) -> bool:
    return all(e.get("state") in ("PASS", "FAIL") for e in entries)


def _all_pass(entries: Sequence[Mapping[str, Any]]) -> bool:
    """COMPLETE только когда каждый check PASS (FAIL останавливает frontier)."""
    return bool(entries) and all(e.get("state") == "PASS" for e in entries)


def _any_fail(entries: Sequence[Mapping[str, Any]]) -> bool:
    return any(e.get("state") == "FAIL" for e in entries)


def _any_fail_blocking_frontier(
    change_id: str,
    entries: Sequence[Mapping[str, Any]],
    *,
    root: Path,
) -> bool:
    from bounded_verify_break_glass import ledger_fail_blocks_frontier

    for entry in entries:
        if entry.get("state") != "FAIL":
            continue
        check_id = str(entry.get("check_id", ""))
        if ledger_fail_blocks_frontier(change_id, check_id, root=root):
            return True
    return False


def _first_pending(entries: Sequence[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    pending = [e for e in entries if e.get("state") == "PENDING"]
    if not pending:
        return None
    pending.sort(key=lambda x: int(x["order"]))
    return dict(pending[0])


def _next_assignment_epoch(ledger: Mapping[str, Any]) -> int:
    """Следующий assignment_epoch: terminal_count + 1 (монотонный per ledger)."""
    terminal_count = sum(
        1 for e in ledger["entries"] if e.get("state") in ("PASS", "FAIL")
    )
    assignment = ledger.get("current_assignment")
    if isinstance(assignment, dict):
        return max(terminal_count + 1, int(assignment["assignment_epoch"]) + 1)
    return terminal_count + 1 if terminal_count > 0 else 1


def _set_current(
    ledger: MutableMapping[str, Any],
    check_id: str,
    assignment_epoch: int,
) -> None:
    for entry in ledger["entries"]:
        if entry["check_id"] == check_id:
            entry["state"] = "CURRENT"
        elif entry.get("state") == "CURRENT":
            entry["state"] = "PENDING"
    ledger["current_assignment"] = {
        "check_id": check_id,
        "assignment_epoch": assignment_epoch,
    }
    ledger["ledger_status"] = "ACTIVE"


def _try_drain_current(
    change_id: str,
    plan: Mapping[str, Any],
    ledger: MutableMapping[str, Any],
    *,
    root: Path,
    enforce_plan_fingerprint: bool,
) -> bool:
    """Drain CURRENT если valid result; возвращает True если ledger изменён."""
    if _count_current_entries(ledger["entries"]) != 1:
        return False

    assignment_raw = ledger.get("current_assignment")
    if not isinstance(assignment_raw, dict):
        raise BoundedVerifyLedgerError(LEDGER_INVALID, "missing current_assignment")

    plan_fp = str(plan["plan_fingerprint"])
    lookup_fp = plan_fp if enforce_plan_fingerprint else "0" * 64
    content_digest = content_digest_required_for_drain(plan)
    if (
        str(plan.get("schema_version", "")) == "bounded-verify-plan-v2"
        and content_digest is not None
        and len(content_digest) != 64
    ):
        raise BoundedVerifyLedgerError(
            LEDGER_INVALID,
            "v2 plan requires valid 64-char implementation_snapshot.content_digest",
        )

    expected = ExpectedAssignment(
        check_id=str(assignment_raw["check_id"]),
        assignment_epoch=int(assignment_raw["assignment_epoch"]),
        ledger_revision=int(ledger["ledger_revision"]),
        plan_fingerprint=lookup_fp,
        content_digest=content_digest,
    )

    try:
        result = find_result_for_current(change_id, expected, root=root)
    except BoundedVerifyResultError as exc:
        raise BoundedVerifyLedgerError(exc.code, exc.message) from exc
    if result is None:
        return False

    real_expected = ExpectedAssignment(
        check_id=expected.check_id,
        assignment_epoch=expected.assignment_epoch,
        ledger_revision=expected.ledger_revision,
        plan_fingerprint=plan_fp,
        content_digest=content_digest,
    )
    decision = validate_result_for_drain(result, real_expected)
    if not decision.allowed:
        raise BoundedVerifyLedgerError(decision.code, decision.message)

    terminal = decision.terminal_state
    worker_packet = result.get("worker_packet_or_equivalent")
    if not isinstance(worker_packet, dict):
        worker_packet = result.get("worker_packet")
    for entry in ledger["entries"]:
        if entry.get("state") == "CURRENT":
            entry["state"] = terminal
            if terminal == "FAIL":
                entry["last_fail_ledger_revision"] = int(ledger["ledger_revision"])
            else:
                entry["accepted_result_id"] = str(result["result_id"])
                entry["accepted_execution_id"] = str(result["execution_id"])
                entry["accepted_assignment_epoch"] = int(result["assignment_epoch"])
                entry["accepted_ledger_revision"] = int(result["ledger_revision"])
                if content_digest is not None:
                    entry["accepted_content_digest"] = content_digest

    ledger["current_assignment"] = None
    ledger["ledger_revision"] = int(ledger["ledger_revision"]) + 1

    if _all_pass(ledger["entries"]):
        ledger["ledger_status"] = "COMPLETE"
        from bounded_verify_plan_profile import ledger_completion_attestation

        ledger["completion_attestation"] = ledger_completion_attestation(plan)
    return True


def schedule_write(
    change_id: str,
    *,
    root: Path = REPO_ROOT,
    write: bool = True,
    expected_revision: Optional[int] = None,
    enforce_plan_fingerprint: bool = True,
    test_hook: Optional[Mapping[str, Any]] = None,
) -> ScheduleWriteResult:
    """Scheduler hop: lock → validate plan → drain → select CURRENT → publish.

    Args:
        change_id: OpenSpec change id.
        root: Repo root for hermetic tests.
        write: Публиковать ledger при мутации.
        expected_revision: CAS guard (B08).
        enforce_plan_fingerprint: False только для fault-injection (X-COMP-03).
        test_hook: ``crash_before_replace`` для crash/replay.

    Returns:
        ScheduleWriteResult; ``check_required`` iff CHECK_REQUIRED emitted (B05).

    Raises:
        BoundedVerifyLedgerError: fail-closed per design.
    """
    lock_path = scheduler_lock_path(change_id, root=root)

    with _scheduler_lock(lock_path):
        plan = _require_frozen_plan(change_id, root=root)
        existing = _read_existing_ledger(change_id, root=root)
        _assert_ledger_plan_bind(plan, existing)

        if existing is None:
            working: dict[str, Any] = _init_ledger_from_plan(plan)
        else:
            working = deepcopy(dict(existing))
            _assert_entries_subset_of_plan(plan, working)

        read_revision = int(working["ledger_revision"])
        if expected_revision is not None and read_revision != expected_revision:
            raise BoundedVerifyLedgerError(
                LEDGER_STALE_REVISION,
                f"expected revision {expected_revision}, got {read_revision}",
            )

        # Drain outstanding CURRENT before snapshot drift blocks ledger progress
        # (supersession recovery on intentional baseline drift).
        if isinstance(working.get("current_assignment"), dict):
            if _try_drain_current(
                change_id,
                plan,
                working,
                root=root,
                enforce_plan_fingerprint=enforce_plan_fingerprint,
            ):
                if _any_fail(working["entries"]):
                    if write:
                        def _validate_pre_fail(payload: Mapping[str, Any]) -> None:
                            _validate_ledger_document(payload)

                        atomic_publish_json(
                            ledger_carrier_path(change_id, root=root),
                            working,
                            validate=_validate_pre_fail,
                            test_hook=test_hook,
                        )
                    failed_id = next(
                        str(e["check_id"])
                        for e in working["entries"]
                        if e.get("state") == "FAIL"
                    )
                    return ScheduleWriteResult(
                        change_id=change_id,
                        ledger_revision=int(working["ledger_revision"]),
                        ledger_status=str(working["ledger_status"]),
                        current_check_id=failed_id,
                        assignment_epoch=None,
                        check_required=False,
                        code="VERIFICATION_CHECK_FAILED",
                        message="frontier stopped on FAIL entry",
                    )
                if working.get("current_assignment") is None and write:
                    def _validate_pre_drain(payload: Mapping[str, Any]) -> None:
                        _validate_ledger_document(payload)

                    atomic_publish_json(
                        ledger_carrier_path(change_id, root=root),
                        working,
                        validate=_validate_pre_drain,
                        test_hook=test_hook,
                    )
                if working.get("current_assignment") is None:
                    return ScheduleWriteResult(
                        change_id=change_id,
                        ledger_revision=int(working["ledger_revision"]),
                        ledger_status=str(working["ledger_status"]),
                        current_check_id=None,
                        assignment_epoch=None,
                        check_required=False,
                        code="OUTSTANDING_ASSIGNMENT_DRAINED",
                        message="CURRENT assignment drained before freshness gate",
                    )

        try:
            assert_snapshot_fresh(plan, change_id, root=root)
            from bounded_verify_s5_obligation_applicability import (
                assert_conditional_applicability_current,
            )

            assert_conditional_applicability_current(plan, change_id, root=root)
        except BoundedVerifyPlanError as drift_exc:
            if drift_exc.code in (
                "BLOCKED_SNAPSHOT_DRIFT",
                "BLOCKED_CONDITIONAL_APPLICABILITY_DRIFT",
            ):
                raise BoundedVerifyLedgerError(
                    drift_exc.code,
                    drift_exc.message,
                ) from drift_exc
            raise

        before_revision = read_revision
        before_assignment = deepcopy(working.get("current_assignment"))
        mutated = False

        if _try_drain_current(
            change_id,
            plan,
            working,
            root=root,
            enforce_plan_fingerprint=enforce_plan_fingerprint,
        ):
            mutated = True

        if _any_fail_blocking_frontier(change_id, working["entries"], root=root):
            if mutated and write:
                def _validate_fail_stop(payload: Mapping[str, Any]) -> None:
                    _validate_ledger_document(payload)

                atomic_publish_json(
                    ledger_carrier_path(change_id, root=root),
                    working,
                    validate=_validate_fail_stop,
                    test_hook=test_hook,
                )
            failed_id = next(
                str(e["check_id"])
                for e in working["entries"]
                if e.get("state") == "FAIL"
            )
            return ScheduleWriteResult(
                change_id=change_id,
                ledger_revision=int(working["ledger_revision"]),
                ledger_status=str(working["ledger_status"]),
                current_check_id=failed_id,
                assignment_epoch=None,
                check_required=False,
                code="VERIFICATION_CHECK_FAILED",
                message="frontier stopped on FAIL entry",
            )

        if working["ledger_status"] == "COMPLETE":
            if mutated and write:
                def _validate_complete(payload: Mapping[str, Any]) -> None:
                    _validate_ledger_document(payload)

                atomic_publish_json(
                    ledger_carrier_path(change_id, root=root),
                    working,
                    validate=_validate_complete,
                    test_hook=test_hook,
                )
            return ScheduleWriteResult(
                change_id=change_id,
                ledger_revision=int(working["ledger_revision"]),
                ledger_status="COMPLETE",
                current_check_id=None,
                assignment_epoch=None,
                check_required=False,
                code=NO_PENDING_CHECKS,
            )

        if _count_current_entries(working["entries"]) == 0:
            nxt = _first_pending(working["entries"])
            if nxt is None:
                if _all_pass(working["entries"]):
                    working["ledger_status"] = "COMPLETE"
                working["current_assignment"] = None
                if write:
                    def _validate_done(payload: Mapping[str, Any]) -> None:
                        _validate_ledger_document(payload)

                    atomic_publish_json(
                        ledger_carrier_path(change_id, root=root),
                        working,
                        validate=_validate_done,
                        test_hook=test_hook,
                    )
                return ScheduleWriteResult(
                    change_id=change_id,
                    ledger_revision=int(working["ledger_revision"]),
                    ledger_status="COMPLETE",
                    current_check_id=None,
                    assignment_epoch=None,
                    check_required=False,
                    code=NO_PENDING_CHECKS,
                )

            epoch = _next_assignment_epoch(working)
            _set_current(working, str(nxt["check_id"]), epoch)
            mutated = True

        assignment = working.get("current_assignment")
        if not isinstance(assignment, dict):
            raise BoundedVerifyLedgerError(LEDGER_INVALID, "CURRENT without assignment")

        current_check_id = str(assignment["check_id"])
        assignment_epoch = int(assignment["assignment_epoch"])

        if (
            not mutated
            and before_assignment == assignment
            and before_revision == int(working["ledger_revision"])
        ):
            # B07: idempotent replay — ledger unchanged, CHECK_REQUIRED re-emitted.
            return ScheduleWriteResult(
                change_id=change_id,
                ledger_revision=int(working["ledger_revision"]),
                ledger_status=str(working["ledger_status"]),
                current_check_id=current_check_id,
                assignment_epoch=assignment_epoch,
                check_required=True,
                code=CHECK_REQUIRED,
            )

        if mutated and write:
            def _validate_publish(payload: Mapping[str, Any]) -> None:
                _validate_ledger_document(payload)

            atomic_publish_json(
                ledger_carrier_path(change_id, root=root),
                working,
                validate=_validate_publish,
                test_hook=test_hook,
            )

        return ScheduleWriteResult(
            change_id=change_id,
            ledger_revision=int(working["ledger_revision"]),
            ledger_status=str(working["ledger_status"]),
            current_check_id=current_check_id,
            assignment_epoch=assignment_epoch,
            check_required=True,
            code=CHECK_REQUIRED,
        )


def _cli_main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: ``schedule --change <id> --write`` per TARGET_REALIZATION_MAP."""
    parser = argparse.ArgumentParser(prog="bounded_verify_ledger")
    sub = parser.add_subparsers(dest="command", required=True)

    sched = sub.add_parser("schedule")
    sched.add_argument("--change", required=True, dest="change_id")
    sched.add_argument("--write", action="store_true")
    sched.add_argument("--expected-revision", type=int, default=None)

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "schedule":
        try:
            result = schedule_write(
                args.change_id,
                write=bool(args.write),
                expected_revision=args.expected_revision,
            )
        except BoundedVerifyLedgerError as exc:
            print(f"ERROR {exc.code}: {exc.message}", file=sys.stderr)
            return 1

        if result.check_required:
            print(
                f"CHECK_REQUIRED check_id={result.current_check_id} "
                f"assignment_epoch={result.assignment_epoch} "
                f"ledger_revision={result.ledger_revision}"
            )
        else:
            print(f"{result.code} ledger_revision={result.ledger_revision}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_cli_main())
