# -*- coding: utf-8 -*-
"""SLICE-003 behavioral tests (C01–C07) for bounded verification executor.

Покрывает INDIVIDUAL_ACQUISITION_C: CURRENT-only execution, refusal paths,
exclusive result create, no ledger/plan/registry writes.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(BV_ROOT))

from bounded_verify_executor import (  # noqa: E402
    ASSIGNMENT_MISMATCH,
    ASSIGNMENT_NOT_CURRENT,
    BoundedVerifyExecutorError,
    NO_CURRENT_ASSIGNMENT,
    run_current_check,
)
from bounded_verify_ledger import (  # noqa: E402
    ledger_carrier_path,
    schedule_write,
)
from bounded_verify_plan import (  # noqa: E402
    draft_plan,
    freeze_plan,
    plan_carrier_path,
)
from bounded_verify_result import (  # noqa: E402
    RESULT_ALREADY_EXISTS,
    BoundedVerifyResultError,
    ExecutionOutcome,
    ExpectedAssignment,
    create_result,
    result_carrier_path,
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
    from test_impl_seed import seed_hermetic_impl_ok

    cap = root / "opsx" / "project" / "capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx" / "project" / "capabilities.yaml", cap)
    change_id = str(fixture["change_id"])
    change_dir = root / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True, exist_ok=True)
    (change_dir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
    from test_impl_seed import seed_hermetic_impl_ok

    seed_hermetic_impl_ok(change_id, root=root)
    return change_id


def _freeze_and_schedule(root: Path, fixture: dict[str, Any] | None = None):
    fixture = fixture or _load_fixture()
    change_id = _setup_change_tree(root, fixture)
    draft_plan(
        change_id,
        list(fixture["required_checks"]),
        root=root,
        registry_path=_registry_path(),
    )
    freeze_plan(change_id, root=root, registry_path=_registry_path())
    sched = schedule_write(change_id, root=root)
    plan = json.loads(
        plan_carrier_path(change_id, root=root).read_text(encoding="utf-8")
    )
    return change_id, sched, plan


def _pass_runner(
    check_id: str,
    _registry_path: Path,
    _root: Path,
) -> tuple[str, dict[str, Any]]:
    return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS", "EXIT_CODE": 0}


def _c01_executes_current_exclusive_create() -> tuple[bool, str]:
    """C01: run-bounded-verify-check executes CURRENT and exclusive-creates result."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, sched, _plan = _freeze_and_schedule(root)
        assert sched.current_check_id == "pyright-strict"

        result = run_current_check(
            change_id,
            root=root,
            registry_path=_registry_path(),
            check_runner=_pass_runner,
        )
        if result.check_id != "pyright-strict":
            return False, f"check_id={result.check_id}"
        if not result.result_path.is_file():
            return False, "result file missing"
        if result.result_path.stat().st_size == 0:
            return False, "empty result file"

        try:
            run_current_check(
                change_id,
                root=root,
                registry_path=_registry_path(),
                check_runner=_pass_runner,
                execution_id=result.execution_id,
            )
            return False, "duplicate execution_id should refuse"
        except BoundedVerifyResultError as exc:
            if exc.code != RESULT_ALREADY_EXISTS:
                return False, f"collision code={exc.code}"

        return True, "CURRENT executed + exclusive result"


def _c02_non_current_refused() -> tuple[bool, str]:
    """C02: non-CURRENT check refused ASSIGNMENT_NOT_CURRENT."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, sched, _plan = _freeze_and_schedule(root)
        assert sched.current_check_id == "pyright-strict"

        try:
            run_current_check(
                change_id,
                root=root,
                check_id="mypy",
                registry_path=_registry_path(),
                check_runner=_pass_runner,
            )
            return False, "non-CURRENT should refuse"
        except BoundedVerifyExecutorError as exc:
            if exc.code != ASSIGNMENT_NOT_CURRENT:
                return False, f"code={exc.code}"

        return True, "ASSIGNMENT_NOT_CURRENT for in-plan non-CURRENT"


def _c03_stale_assignment_epoch() -> tuple[bool, str]:
    """C03: stale assignment_epoch refused ASSIGNMENT_MISMATCH."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, sched, _plan = _freeze_and_schedule(root)

        try:
            run_current_check(
                change_id,
                root=root,
                assignment_epoch=int(sched.assignment_epoch or 0) + 99,
                registry_path=_registry_path(),
                check_runner=_pass_runner,
            )
            return False, "stale epoch should refuse"
        except BoundedVerifyExecutorError as exc:
            if exc.code != ASSIGNMENT_MISMATCH:
                return False, f"code={exc.code}"

        return True, "ASSIGNMENT_MISMATCH on stale epoch"


def _c04_executor_does_not_write_ledger() -> tuple[bool, str]:
    """C04: executor does not write ledger.json."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, _sched, _plan = _freeze_and_schedule(root)
        ledger_path = ledger_carrier_path(change_id, root=root)
        before = ledger_path.read_text(encoding="utf-8")
        before_mtime = ledger_path.stat().st_mtime_ns

        run_current_check(
            change_id,
            root=root,
            registry_path=_registry_path(),
            check_runner=_pass_runner,
        )

        after = ledger_path.read_text(encoding="utf-8")
        after_mtime = ledger_path.stat().st_mtime_ns
        if before != after or before_mtime != after_mtime:
            return False, "ledger.json mutated"

        return True, "ledger unchanged"


def _c05_executor_does_not_write_plan() -> tuple[bool, str]:
    """C05: executor does not write plan.json."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, _sched, _plan = _freeze_and_schedule(root)
        plan_path = plan_carrier_path(change_id, root=root)
        before = plan_path.read_text(encoding="utf-8")
        before_mtime = plan_path.stat().st_mtime_ns

        run_current_check(
            change_id,
            root=root,
            registry_path=_registry_path(),
            check_runner=_pass_runner,
        )

        after = plan_path.read_text(encoding="utf-8")
        after_mtime = plan_path.stat().st_mtime_ns
        if before != after or before_mtime != after_mtime:
            return False, "plan.json mutated"

        return True, "plan unchanged"


def _c06_executor_does_not_write_registry() -> tuple[bool, str]:
    """C06: executor does not write registry.yaml."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, _sched, _plan = _freeze_and_schedule(root)
        registry = _registry_path()
        before = registry.read_text(encoding="utf-8")
        before_mtime = registry.stat().st_mtime_ns

        run_current_check(
            change_id,
            root=root,
            registry_path=registry,
            check_runner=_pass_runner,
        )

        after = registry.read_text(encoding="utf-8")
        after_mtime = registry.stat().st_mtime_ns
        if before != after or before_mtime != after_mtime:
            return False, "registry mutated"

        return True, "registry unchanged"


def _c07_duplicate_execution_id_collision() -> tuple[bool, str]:
    """C07: duplicate execution_id collision RESULT_ALREADY_EXISTS."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, sched, plan = _freeze_and_schedule(root)
        assignment = ExpectedAssignment(
            check_id=str(sched.current_check_id),
            assignment_epoch=int(sched.assignment_epoch or 0),
            ledger_revision=int(sched.ledger_revision),
            plan_fingerprint=str(plan["plan_fingerprint"]),
        )
        outcome = ExecutionOutcome(
            execution_id="exec-dup-001",
            result_status="PASS",
            worker_packet_or_equivalent={"STATUS": "PASS"},
        )
        first = create_result(change_id, assignment, outcome, root=root)
        if not first.carrier_path.is_file():
            return False, "first result not created"

        try:
            create_result(change_id, assignment, outcome, root=root)
            return False, "second create should refuse"
        except BoundedVerifyResultError as exc:
            if exc.code != RESULT_ALREADY_EXISTS:
                return False, f"code={exc.code}"

        return True, "RESULT_ALREADY_EXISTS on collision"


def _n02_c_before_b() -> tuple[bool, str]:
    """N02: C before B → ASSIGNMENT_NOT_CURRENT."""
    fixture = _load_fixture()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup_change_tree(root, fixture)
        draft_plan(
            change_id,
            list(fixture["required_checks"]),
            root=root,
            registry_path=_registry_path(),
        )
        freeze_plan(change_id, root=root, registry_path=_registry_path())

        try:
            run_current_check(
                change_id,
                root=root,
                registry_path=_registry_path(),
                check_runner=_pass_runner,
            )
            return False, "no ledger CURRENT should refuse"
        except BoundedVerifyExecutorError as exc:
            if exc.code not in (ASSIGNMENT_NOT_CURRENT, NO_CURRENT_ASSIGNMENT):
                return False, f"code={exc.code}"

        return True, "refused without scheduler CURRENT"


def _n05_in_plan_non_current() -> tuple[bool, str]:
    """N05: executor in-plan non-CURRENT → ASSIGNMENT_NOT_CURRENT."""
    return _c02_non_current_refused()


def build_cases() -> list[tuple[str, Callable[[], tuple[bool, str]]]]:
    return [
        ("C01", _c01_executes_current_exclusive_create),
        ("C02", _c02_non_current_refused),
        ("C03", _c03_stale_assignment_epoch),
        ("C04", _c04_executor_does_not_write_ledger),
        ("C05", _c05_executor_does_not_write_plan),
        ("C06", _c06_executor_does_not_write_registry),
        ("C07", _c07_duplicate_execution_id_collision),
        ("N02", _n02_c_before_b),
        ("N05", _n05_in_plan_non_current),
    ]


def main() -> int:
    results: list[Result] = []
    for case_id, fn in build_cases():
        ok, detail = fn()
        results.append((case_id, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]

    print(f"BOUNDED_VERIFY_EXECUTOR_TESTS: {passed}/{len(results)} passed")
    for case_id, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {case_id}: {detail}")
    if failed:
        print("FAILED_IDS:", ", ".join(r[0] for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
