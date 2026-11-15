# -*- coding: utf-8 -*-
"""Atomic publication tests (AT01–AT04) for bounded verification carriers.

Покрывает Protocol A: partial temp siblings, crash-before-replace, exclusive
result create — readers never accept partial or malformed canonical artifacts.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"

sys.path.insert(0, str(BV_ROOT))

from bounded_verify_atomic import (  # noqa: E402
    atomic_publish_json,
    is_canonical_plan_readable,
    read_canonical_json,
)
from bounded_verify_ledger import (  # noqa: E402
    LEDGER_SCHEMA_VERSION,
    _validate_ledger_document,
    ledger_carrier_path,
    read_ledger,
)
from bounded_verify_plan import plan_carrier_path  # noqa: E402
from bounded_verify_result import (  # noqa: E402
    BoundedVerifyResultError,
    ExpectedAssignment,
    ExecutionOutcome,
    create_result,
    read_result,
    result_carrier_path,
)

Result = tuple[str, bool, str]

PLAN_FP = "c" * 64


def _at01_partial_plan_temp_not_canonical() -> tuple[bool, str]:
    """AT01: partial plan temp sibling не читается как canonical plan."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan_path = root / "plan.json"
        partial = root / "plan.json.tmp.99999.deadbeef"
        partial.write_text('{"plan_status": "FROZEN", "truncated":', encoding="utf-8")

        if is_canonical_plan_readable(plan_path):
            return False, "absent canonical readable"
        if read_canonical_json(plan_path) is not None:
            return False, "absent canonical returned doc"

        return True, "partial plan temp ignored"


def _at02_partial_ledger_temp_not_canonical() -> tuple[bool, str]:
    """AT02: partial ledger temp sibling не читается как canonical ledger."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "at02-ledger-temp"
        carrier = ledger_carrier_path(change_id, root=root)
        carrier.parent.mkdir(parents=True, exist_ok=True)
        partial = carrier.parent / "ledger.json.tmp.99999.deadbeef"
        partial.write_text('{"ledger_status": "ACTIVE", "incomplete":', encoding="utf-8")

        if read_canonical_json(carrier) is not None:
            return False, "absent ledger readable via partial temp"

        good = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "ledger_id": "led-at02",
            "plan_id": "plan-at02",
            "plan_fingerprint": PLAN_FP,
            "ledger_revision": 1,
            "ledger_status": "ACTIVE",
            "entries": [
                {"check_id": "pyright-strict", "order": 1, "state": "CURRENT"},
            ],
            "current_assignment": {
                "check_id": "pyright-strict",
                "assignment_epoch": 1,
            },
        }

        def _validate(doc: dict[str, Any]) -> None:
            _validate_ledger_document(doc)

        atomic_publish_json(carrier, good, validate=_validate)

        try:
            read_ledger(change_id, root=root)
        except Exception as exc:
            return False, f"valid ledger unreadable: {exc}"

        partial.write_text("{broken", encoding="utf-8")
        if read_canonical_json(carrier) is None:
            return False, "canonical ledger corrupted by temp sibling write"

        return True, "partial ledger temp ignored; canonical ledger readable"


def _at03_partial_result_exclusive_create_no_canonical() -> tuple[bool, str]:
    """AT03: partial/collision result не становится canonical drain input."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "at03-result-partial"
        assignment = ExpectedAssignment(
            check_id="pyright-strict",
            assignment_epoch=1,
            ledger_revision=1,
            plan_fingerprint=PLAN_FP,
        )
        outcome = ExecutionOutcome(
            execution_id="exec-at03",
            result_status="PASS",
            worker_packet_or_equivalent={"STATUS": "PASS"},
        )
        record = create_result(change_id, assignment, outcome, root=root)
        if not record.carrier_path.is_file():
            return False, "exclusive create failed"

        partial = record.carrier_path.with_name(
            f"{record.carrier_path.name}.tmp.partial"
        )
        partial.write_text('{"result_status": "PASS", "truncated":', encoding="utf-8")

        try:
            read_result(change_id, record.result_id, root=root)
        except BoundedVerifyResultError:
            return False, "valid result unreadable after partial sibling"

        try:
            create_result(change_id, assignment, outcome, root=root)
            return False, "duplicate create should refuse"
        except BoundedVerifyResultError as exc:
            if exc.code != "RESULT_ALREADY_EXISTS":
                return False, f"collision code={exc.code}"

        corrupt = record.carrier_path
        corrupt.write_text("{not valid", encoding="utf-8")
        try:
            read_result(change_id, record.result_id, root=root)
            return False, "malformed result accepted"
        except BoundedVerifyResultError:
            pass

        return True, "exclusive create + partial/malformed not canonical"


def _at04_plan_crash_before_replace_preserves_prior() -> tuple[bool, str]:
    """AT04: os.replace crash simulation сохраняет prior canonical plan state."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan_path = root / "plan.json"
        prior = {
            "schema_version": "bounded-verify-plan-v1",
            "change_id": "at04",
            "plan_id": "p-prior",
            "plan_status": "DRAFT",
            "plan_fingerprint": "",
            "required_checks": [{"check_id": "pyright-strict", "order": 1}],
            "implementation_snapshot": {"content_digest": "abc123"},
        }
        plan_path.write_text(json.dumps(prior, sort_keys=True), encoding="utf-8")

        frozen = dict(prior)
        frozen["plan_status"] = "FROZEN"
        frozen["plan_fingerprint"] = "d" * 64

        def _validate(doc: dict[str, Any]) -> None:
            if doc.get("plan_status") != "FROZEN":
                raise ValueError("not frozen")

        atomic_publish_json(
            plan_path,
            frozen,
            validate=_validate,
            test_hook={"crash_before_replace": True},
        )

        after = json.loads(plan_path.read_text(encoding="utf-8"))
        if after["plan_status"] != "DRAFT":
            return False, "prior DRAFT mutated after crash-before-replace"

        atomic_publish_json(plan_path, frozen, validate=_validate)
        final = json.loads(plan_path.read_text(encoding="utf-8"))
        if final["plan_status"] != "FROZEN":
            return False, "replay publish failed"

        return True, "crash preserved prior; replay published FROZEN"


def build_cases() -> list[tuple[str, Callable[[], tuple[bool, str]]]]:
    return [
        ("AT01", _at01_partial_plan_temp_not_canonical),
        ("AT02", _at02_partial_ledger_temp_not_canonical),
        ("AT03", _at03_partial_result_exclusive_create_no_canonical),
        ("AT04", _at04_plan_crash_before_replace_preserves_prior),
    ]


def main() -> int:
    results: list[Result] = []
    for case_id, fn in build_cases():
        ok, detail = fn()
        results.append((case_id, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]

    print(f"BOUNDED_VERIFY_ATOMIC_PUBLICATION_TESTS: {passed}/{len(results)} passed")
    for case_id, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {case_id}: {detail}")
    if failed:
        print("FAILED_IDS:", ", ".join(r[0] for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
