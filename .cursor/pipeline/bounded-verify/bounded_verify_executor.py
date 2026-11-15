# -*- coding: utf-8 -*-
"""CURRENT-only bounded verification executor (R-V1-005C / SLICE-003).

Выполняет только exact ledger CURRENT; пишет immutable result evidence.
Не schedule, не пишет ledger/plan/registry.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

import yaml

from bounded_verify_ledger import (
    LEDGER_INVALID,
    LEDGER_NOT_FOUND,
    LEDGER_STALE_PLAN_IDENTITY,
    BoundedVerifyLedgerError,
    read_ledger,
)
from bounded_verify_plan import (
    DEFAULT_REGISTRY_PATH,
    PLAN_NOT_FOUND,
    REGISTRY_CHECK_UNKNOWN,
    BoundedVerifyPlanError,
    read_plan,
)
from bounded_verify_result import (
    ExecutionOutcome,
    ExpectedAssignment,
    ResultRecord,
    create_result,
)
from bounded_verify_runtime_preflight import PreflightOutcome, preflight_static_tools
from bounded_verify_s5_adapters import _ADAPTER_BY_ID, run_s5_adapter  # noqa: F401

ASSIGNMENT_NOT_CURRENT = "ASSIGNMENT_NOT_CURRENT"
ASSIGNMENT_MISMATCH = "ASSIGNMENT_MISMATCH"
EXECUTOR_BLOCKED = "EXECUTOR_BLOCKED"
NO_CURRENT_ASSIGNMENT = "NO_CURRENT_ASSIGNMENT"

REPO_ROOT = Path(__file__).resolve().parents[3]

CheckRunner = Callable[[str, Path, Path], tuple[str, Mapping[str, Any]]]


class BoundedVerifyExecutorError(Exception):
    """Ошибка bounded verification executor."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class RunCurrentCheckResult:
    """Результат ``run_current_check`` — immutable execution evidence."""

    change_id: str
    check_id: str
    execution_id: str
    result_id: str
    result_status: str
    ledger_revision: int
    assignment_epoch: int
    plan_fingerprint: str
    result_path: Path


def _count_current_entries(entries: Sequence[Mapping[str, Any]]) -> int:
    return sum(1 for e in entries if e.get("state") == "CURRENT")


def _resolve_current_assignment(
    change_id: str,
    *,
    root: Path,
    check_id: Optional[str],
    assignment_epoch: Optional[int],
    ledger_revision: Optional[int],
    enforce_current: bool,
) -> tuple[dict[str, Any], dict[str, Any], ExpectedAssignment]:
    """Читает plan+ledger и возвращает frozen CURRENT identity."""
    try:
        plan = read_plan(change_id, root=root)
    except BoundedVerifyPlanError as exc:
        if exc.code == PLAN_NOT_FOUND:
            raise BoundedVerifyExecutorError(
                ASSIGNMENT_NOT_CURRENT,
                "plan not found",
            ) from exc
        raise BoundedVerifyExecutorError(exc.code, exc.message) from exc

    if plan.get("plan_status") != "FROZEN":
        raise BoundedVerifyExecutorError(
            ASSIGNMENT_NOT_CURRENT,
            "plan is not FROZEN",
        )

    from bounded_verify_s5_obligation_applicability import assert_conditional_applicability_current

    try:
        assert_conditional_applicability_current(plan, change_id, root=root)
    except BoundedVerifyPlanError as exc:
        raise BoundedVerifyExecutorError(EXECUTOR_BLOCKED, exc.message) from exc

    try:
        ledger = read_ledger(change_id, root=root)
    except BoundedVerifyLedgerError as exc:
        if exc.code in (LEDGER_NOT_FOUND, LEDGER_INVALID):
            raise BoundedVerifyExecutorError(
                ASSIGNMENT_NOT_CURRENT,
                exc.message,
            ) from exc
        raise BoundedVerifyExecutorError(exc.code, exc.message) from exc

    if ledger.get("plan_fingerprint") != plan.get("plan_fingerprint"):
        raise BoundedVerifyExecutorError(
            LEDGER_STALE_PLAN_IDENTITY,
            "ledger plan_fingerprint mismatch",
        )
    if ledger.get("plan_id") != plan.get("plan_id"):
        raise BoundedVerifyExecutorError(
            LEDGER_STALE_PLAN_IDENTITY,
            "ledger plan_id mismatch",
        )

    if ledger.get("ledger_status") != "ACTIVE":
        raise BoundedVerifyExecutorError(
            NO_CURRENT_ASSIGNMENT,
            "ledger is not ACTIVE",
        )

    entries = ledger.get("entries", [])
    if _count_current_entries(entries) != 1:
        raise BoundedVerifyExecutorError(
            NO_CURRENT_ASSIGNMENT,
            "exactly one CURRENT required",
        )

    assignment_raw = ledger.get("current_assignment")
    if not isinstance(assignment_raw, dict):
        raise BoundedVerifyExecutorError(
            NO_CURRENT_ASSIGNMENT,
            "missing current_assignment",
        )

    current_check_id = str(assignment_raw["check_id"])
    current_epoch = int(assignment_raw["assignment_epoch"])
    current_revision = int(ledger["ledger_revision"])
    plan_fp = str(plan["plan_fingerprint"])

    if enforce_current and check_id is not None and check_id != current_check_id:
        raise BoundedVerifyExecutorError(
            ASSIGNMENT_NOT_CURRENT,
            f"check_id {check_id!r} is not CURRENT {current_check_id!r}",
        )

    if assignment_epoch is not None and assignment_epoch != current_epoch:
        raise BoundedVerifyExecutorError(
            ASSIGNMENT_MISMATCH,
            f"assignment_epoch mismatch: expected {current_epoch}",
        )

    if ledger_revision is not None and ledger_revision != current_revision:
        raise BoundedVerifyExecutorError(
            ASSIGNMENT_MISMATCH,
            f"ledger_revision mismatch: expected {current_revision}",
        )

    snap = plan.get("implementation_snapshot")
    content_digest = None
    if isinstance(snap, dict):
        raw = snap.get("content_digest")
        if isinstance(raw, str) and len(raw) == 64:
            content_digest = raw

    expected = ExpectedAssignment(
        check_id=current_check_id,
        assignment_epoch=current_epoch,
        ledger_revision=current_revision,
        plan_fingerprint=plan_fp,
        content_digest=content_digest,
    )
    return plan, ledger, expected


def _load_registry_check(
    check_id: str,
    registry_path: Path,
) -> Mapping[str, Any]:
    if not registry_path.is_file():
        raise BoundedVerifyExecutorError(
            EXECUTOR_BLOCKED,
            "registry not found",
        )
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BoundedVerifyExecutorError(EXECUTOR_BLOCKED, "registry invalid")
    checks = data.get("checks")
    if not isinstance(checks, dict) or check_id not in checks:
        raise BoundedVerifyExecutorError(
            REGISTRY_CHECK_UNKNOWN,
            f"unknown check_id: {check_id}",
        )
    entry = checks[check_id]
    if not isinstance(entry, dict):
        raise BoundedVerifyExecutorError(EXECUTOR_BLOCKED, "registry entry invalid")
    return entry


def _default_check_runner(
    check_id: str,
    registry_path: Path,
    root: Path,
    *,
    change_id: str,
) -> tuple[str, dict[str, Any]]:
    """Registry-backed HOW lookup + subprocess invoke (read-only registry)."""
    entry = _load_registry_check(check_id, registry_path)
    pack_rel = str(entry.get("pack", ""))
    if not pack_rel:
        raise BoundedVerifyExecutorError(EXECUTOR_BLOCKED, "registry pack missing")

    from bounded_verify_registry_execution import (
        RegistryExecutionSupport,
        resolve_registry_execution_contract,
    )

    contract = resolve_registry_execution_contract(check_id, registry_path, root=root)
    if contract.support == RegistryExecutionSupport.UNSUPPORTED:
        raise BoundedVerifyExecutorError(
            EXECUTOR_BLOCKED,
            f"registry check unsupported for bounded subprocess: {contract.reason}",
        )
    if contract.support != RegistryExecutionSupport.MACHINE_EXECUTABLE or not contract.argv:
        raise BoundedVerifyExecutorError(
            EXECUTOR_BLOCKED,
            contract.reason or "registry execution blocked",
        )
    cmd = list(contract.argv)

    run_env = {**os.environ, "BOUNDED_VERIFY_CHANGE_ID": change_id}
    completed = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        env=run_env,
    )
    packet: dict[str, Any] = {
        "CHECK_ID": check_id,
        "COMMAND": " ".join(cmd),
        "EXIT_CODE": completed.returncode,
        "STATUS": "PASS" if completed.returncode == 0 else "FAIL",
    }
    if completed.returncode == 0:
        return "PASS", packet
    if completed.returncode in (127, 126):
        return "EXECUTION_ERROR", {**packet, "STATUS": "BLOCKED"}
    if completed.returncode == 3:
        # Owner-gated / receipt BLOCKED (exit 3) is drainable FAIL, not EXECUTION_ERROR.
        return "FAIL", {**packet, "STATUS": "BLOCKED"}
    return "FAIL", packet


def run_current_check(
    change_id: str,
    *,
    root: Path = REPO_ROOT,
    check_id: Optional[str] = None,
    assignment_epoch: Optional[int] = None,
    ledger_revision: Optional[int] = None,
    execution_id: Optional[str] = None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    check_runner: Optional[CheckRunner] = None,
    enforce_current: bool = True,
) -> RunCurrentCheckResult:
    """Выполняет exact ledger CURRENT и exclusive-creates result evidence.

    Args:
        change_id: OpenSpec change id.
        root: Repo root для hermetic tests.
        check_id: Если задан — должен совпадать с CURRENT (C02).
        assignment_epoch: Опциональный bind guard (C03).
        ledger_revision: Опциональный bind guard.
        execution_id: uuid4 per invocation; fresh для retry (C15).
        registry_path: Read-only registry HOW authority (C10).
        check_runner: Test injection; bypasses subprocess.
        enforce_current: False только для fault-injection (X-COMP-04).

    Returns:
        RunCurrentCheckResult; result PASS ≠ aggregate verification PASS (C16).

    Raises:
        BoundedVerifyExecutorError: fail-closed per design.
    """
    _plan, _ledger, expected = _resolve_current_assignment(
        change_id,
        root=root,
        check_id=check_id,
        assignment_epoch=assignment_epoch,
        ledger_revision=ledger_revision,
        enforce_current=enforce_current,
    )

    preflight = preflight_static_tools()
    if preflight.outcome != PreflightOutcome.READY:
        raise BoundedVerifyExecutorError(EXECUTOR_BLOCKED, preflight.message)

    exec_id = execution_id or str(uuid.uuid4())
    runner = check_runner
    if runner is None and expected.check_id in _ADAPTER_BY_ID:
        def _s5_runner(cid: str, _reg: Path, r: Path) -> tuple[str, Mapping[str, Any]]:
            return run_s5_adapter(cid, change_id, root=r)

        runner = _s5_runner
    if runner is None:
        runner = _default_check_runner

    try:
        result_status, worker_packet = runner(
            expected.check_id,
            registry_path,
            root,
        )
    except BoundedVerifyExecutorError:
        raise
    except Exception as exc:
        result_status = "EXECUTION_ERROR"
        worker_packet = {
            "CHECK_ID": expected.check_id,
            "STATUS": "ERROR",
            "ERROR": str(exc),
        }

    record: ResultRecord = create_result(
        change_id,
        expected,
        ExecutionOutcome(
            execution_id=exec_id,
            result_status=result_status,
            worker_packet_or_equivalent=worker_packet,
            registry_check_id_snapshot=expected.check_id,
        ),
        root=root,
    )

    return RunCurrentCheckResult(
        change_id=change_id,
        check_id=expected.check_id,
        execution_id=exec_id,
        result_id=record.result_id,
        result_status=record.result_status,
        ledger_revision=expected.ledger_revision,
        assignment_epoch=expected.assignment_epoch,
        plan_fingerprint=expected.plan_fingerprint,
        result_path=record.carrier_path,
    )


def _cli_main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI: ``run --change <id>`` per TARGET_REALIZATION_MAP."""
    parser = argparse.ArgumentParser(prog="bounded_verify_executor")
    sub = parser.add_subparsers(dest="command", required=True)

    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--mode", required=True)
    run_cmd.add_argument("--change", required=True, dest="change_id")
    run_cmd.add_argument("--check-id", default=None)
    run_cmd.add_argument("--assignment-epoch", type=int, default=None)
    run_cmd.add_argument("--ledger-revision", type=int, default=None)

    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "run":
        if args.mode != "bounded":
            print(
                "ERROR MODE_NOT_BOUNDED: mode must be bounded",
                file=sys.stderr,
            )
            return 1
        try:
            result = run_current_check(
                args.change_id,
                check_id=args.check_id,
                assignment_epoch=args.assignment_epoch,
                ledger_revision=args.ledger_revision,
            )
        except BoundedVerifyExecutorError as exc:
            print(f"ERROR {exc.code}: {exc.message}", file=sys.stderr)
            return 1

        print(
            f"RESULT_RECORDED check_id={result.check_id} "
            f"result_id={result.result_id} "
            f"result_status={result.result_status} "
            f"execution_id={result.execution_id}"
        )
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_cli_main())
