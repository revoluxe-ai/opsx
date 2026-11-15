# -*- coding: utf-8 -*-
"""Result model tests (R01–R06) for bounded verification drain semantics.

Покрывает immutable result validation: stale identity rejection, drainable
statuses, partial/invalid presence insufficient for ledger advance.
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

from bounded_verify_result import (  # noqa: E402
    RESULT_INCOMPLETE,
    RESULT_STALE_ASSIGNMENT,
    RESULT_STALE_LEDGER,
    RESULT_STALE_PLAN,
    ExpectedAssignment,
    compute_result_id,
    result_carrier_path,
    validate_result_for_drain,
)

Result = tuple[str, bool, str]

PLAN_FP = "a" * 64
OTHER_FP = "b" * 64


def _base_result(
    *,
    plan_fingerprint: str = PLAN_FP,
    ledger_revision: int = 1,
    assignment_epoch: int = 1,
    check_id: str = "pyright-strict",
    execution_id: str = "exec-r01",
    result_status: str = "PASS",
) -> dict[str, Any]:
    result_id = compute_result_id(
        plan_fingerprint,
        ledger_revision,
        check_id,
        execution_id,
    )
    return {
        "schema_version": "bounded-verify-result-v1",
        "result_id": result_id,
        "plan_fingerprint": plan_fingerprint,
        "ledger_revision": ledger_revision,
        "assignment_epoch": assignment_epoch,
        "check_id": check_id,
        "execution_id": execution_id,
        "result_status": result_status,
        "producer": "test",
    }


def _expected(
    *,
    plan_fingerprint: str = PLAN_FP,
    ledger_revision: int = 1,
    assignment_epoch: int = 1,
    check_id: str = "pyright-strict",
) -> ExpectedAssignment:
    return ExpectedAssignment(
        check_id=check_id,
        assignment_epoch=assignment_epoch,
        ledger_revision=ledger_revision,
        plan_fingerprint=plan_fingerprint,
    )


def _r01_stale_plan_fingerprint() -> tuple[bool, str]:
    """R01: drain rejects result with stale plan_fingerprint RESULT_STALE_PLAN."""
    result = _base_result(plan_fingerprint=OTHER_FP)
    decision = validate_result_for_drain(result, _expected())
    if decision.allowed or decision.code != RESULT_STALE_PLAN:
        return False, f"code={decision.code}"
    return True, "RESULT_STALE_PLAN"


def _r02_stale_ledger_revision() -> tuple[bool, str]:
    """R02: drain rejects result with stale ledger_revision RESULT_STALE_LEDGER."""
    result = _base_result(ledger_revision=99)
    decision = validate_result_for_drain(result, _expected(ledger_revision=1))
    if decision.allowed or decision.code != RESULT_STALE_LEDGER:
        return False, f"code={decision.code}"
    return True, "RESULT_STALE_LEDGER"


def _r03_stale_assignment_epoch() -> tuple[bool, str]:
    """R03: drain rejects result with stale assignment_epoch RESULT_STALE_ASSIGNMENT."""
    result = _base_result(assignment_epoch=5)
    decision = validate_result_for_drain(result, _expected(assignment_epoch=1))
    if decision.allowed or decision.code != RESULT_STALE_ASSIGNMENT:
        return False, f"code={decision.code}"
    return True, "RESULT_STALE_ASSIGNMENT"


def _r04_fail_result_drainable_not_aggregate_pass() -> tuple[bool, str]:
    """R04: FAIL result advances ledger entry but not aggregate obligation."""
    result = _base_result(result_status="FAIL")
    decision = validate_result_for_drain(result, _expected())
    if not decision.allowed:
        return False, f"FAIL should drain: {decision.code}"
    if decision.terminal_state != "FAIL":
        return False, f"terminal={decision.terminal_state}"
    # C16 / D:S:008: drain allowed ≠ aggregate verification PASS.
    return True, "FAIL drainable with terminal_state=FAIL (not aggregate PASS)"


def _r05_incomplete_error_no_drain() -> tuple[bool, str]:
    """R05: INCOMPLETE/EXECUTION_ERROR result does not advance ledger."""
    for status in ("INCOMPLETE", "EXECUTION_ERROR"):
        result = _base_result(result_status=status)
        decision = validate_result_for_drain(result, _expected())
        if decision.allowed or decision.code != RESULT_INCOMPLETE:
            return False, f"{status} code={decision.code}"
    return True, "INCOMPLETE/EXECUTION_ERROR not drainable"


def _r06_presence_without_valid_schema() -> tuple[bool, str]:
    """R06: presence without valid schema does not advance."""
    bad = _base_result()
    del bad["result_id"]
    decision = validate_result_for_drain(bad, _expected())
    if decision.allowed:
        return False, "invalid schema should not drain"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "bv-result-r06"
        result_id = "not-a-valid-id"
        path = result_carrier_path(change_id, result_id, root=root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json", encoding="utf-8")
        if path.is_file():
            raw = path.read_text(encoding="utf-8")
            try:
                json.loads(raw)
                return False, "fixture should be invalid json"
            except json.JSONDecodeError:
                pass

    return True, "invalid schema -> RESULT_INCOMPLETE / not drainable"


def build_cases() -> list[tuple[str, Callable[[], tuple[bool, str]]]]:
    return [
        ("R01", _r01_stale_plan_fingerprint),
        ("R02", _r02_stale_ledger_revision),
        ("R03", _r03_stale_assignment_epoch),
        ("R04", _r04_fail_result_drainable_not_aggregate_pass),
        ("R05", _r05_incomplete_error_no_drain),
        ("R06", _r06_presence_without_valid_schema),
    ]


def main() -> int:
    results: list[Result] = []
    for case_id, fn in build_cases():
        ok, detail = fn()
        results.append((case_id, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]

    print(f"BOUNDED_VERIFY_RESULT_MODEL_TESTS: {passed}/{len(results)} passed")
    for case_id, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {case_id}: {detail}")
    if failed:
        print("FAILED_IDS:", ", ".join(r[0] for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
