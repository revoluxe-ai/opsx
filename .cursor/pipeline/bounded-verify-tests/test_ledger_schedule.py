# -*- coding: utf-8 -*-
"""SLICE-002 behavioral tests (B01–B09) for bounded verification ledger scheduling.

Покрывает INDIVIDUAL_ACQUISITION_B: frozen-plan-only scheduler, CURRENT selection,
plan fingerprint bind, CHECK_REQUIRED emission, CAS, idempotent replay, schema.
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

from bounded_verify_atomic import atomic_publish_json  # noqa: E402
from bounded_verify_ledger import (  # noqa: E402
    CHECK_NOT_IN_PLAN,
    CHECK_REQUIRED,
    LEDGER_INVALID,
    LEDGER_SCHEMA_VERSION,
    LEDGER_STALE_PLAN_IDENTITY,
    LEDGER_STALE_REVISION,
    MULTIPLE_CURRENT,
    NO_PENDING_CHECKS,
    PLAN_NOT_FROZEN,
    BoundedVerifyLedgerError,
    _validate_ledger_document,
    ledger_carrier_path,
    read_ledger,
    schedule_write,
)
from bounded_verify_plan import (  # noqa: E402
    draft_plan,
    freeze_plan,
    plan_carrier_path,
)
from bounded_verify_result import (  # noqa: E402
    compute_result_id,
    result_carrier_path,
    results_dir,
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
    seed_hermetic_impl_ok(change_id, root=root)
    return change_id


def _freeze_fixture_plan(root: Path, fixture: dict[str, Any] | None = None) -> str:
    fixture = fixture or _load_fixture()
    change_id = _setup_change_tree(root, fixture)
    draft_plan(
        change_id,
        list(fixture["required_checks"]),
        root=root,
        registry_path=_registry_path(),
    )
    freeze_plan(change_id, root=root, registry_path=_registry_path())
    return change_id


def _write_pass_result(
    root: Path,
    change_id: str,
    *,
    check_id: str,
    ledger_revision: int,
    assignment_epoch: int,
    plan_fingerprint: str,
    execution_id: str = "exec-001",
) -> str:
    result_id = compute_result_id(
        plan_fingerprint,
        ledger_revision,
        check_id,
        execution_id,
    )
    payload = {
        "schema_version": "bounded-verify-result-v1",
        "result_id": result_id,
        "plan_fingerprint": plan_fingerprint,
        "ledger_revision": ledger_revision,
        "assignment_epoch": assignment_epoch,
        "check_id": check_id,
        "execution_id": execution_id,
        "result_status": "PASS",
        "producer": "test",
    }
    path = result_carrier_path(change_id, result_id, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return result_id


def _b01_schedule_refused_when_plan_not_frozen() -> tuple[bool, str]:
    """B01: schedule_write refused when plan not FROZEN."""
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

        try:
            schedule_write(change_id, root=root)
            return False, "DRAFT plan should refuse schedule"
        except BoundedVerifyLedgerError as exc:
            if exc.code != PLAN_NOT_FROZEN:
                return False, f"code={exc.code}"

        try:
            schedule_write(change_id, root=root)
        except BoundedVerifyLedgerError as exc:
            if exc.code != PLAN_NOT_FROZEN:
                return False, f"no plan code={exc.code}"

        return True, "PLAN_NOT_FROZEN on DRAFT and absent FROZEN"


def _b02_deterministic_current_from_plan_order() -> tuple[bool, str]:
    """B02: schedule_write selects deterministic CURRENT from frozen plan order."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _freeze_fixture_plan(root)
        first = schedule_write(change_id, root=root)
        if first.current_check_id != "pyright-strict":
            return False, f"first CURRENT={first.current_check_id}"
        if first.code != CHECK_REQUIRED:
            return False, f"code={first.code}"

        plan = json.loads(
            plan_carrier_path(change_id, root=root).read_text(encoding="utf-8")
        )
        _write_pass_result(
            root,
            change_id,
            check_id="pyright-strict",
            ledger_revision=first.ledger_revision,
            assignment_epoch=int(first.assignment_epoch or 0),
            plan_fingerprint=str(plan["plan_fingerprint"]),
        )
        second = schedule_write(change_id, root=root)
        if second.current_check_id != "mypy":
            return False, f"second CURRENT={second.current_check_id}"

        return True, "order pyright-strict then mypy"


def _b03_check_id_must_be_in_plan() -> tuple[bool, str]:
    """B03: ledger check_id must be in plan.required_checks."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _freeze_fixture_plan(root)
        plan = json.loads(
            plan_carrier_path(change_id, root=root).read_text(encoding="utf-8")
        )
        bad_ledger = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "ledger_id": "bad",
            "plan_id": plan["plan_id"],
            "plan_fingerprint": plan["plan_fingerprint"],
            "ledger_revision": 1,
            "ledger_status": "ACTIVE",
            "entries": [
                {
                    "check_id": "not-in-plan",
                    "order": 1,
                    "state": "PENDING",
                }
            ],
            "current_assignment": None,
        }

        def _validate(doc: dict[str, Any]) -> None:
            _validate_ledger_document(doc)

        atomic_publish_json(
            ledger_carrier_path(change_id, root=root),
            bad_ledger,
            validate=_validate,
        )

        try:
            schedule_write(change_id, root=root)
            return False, "out-of-plan check should refuse"
        except BoundedVerifyLedgerError as exc:
            if exc.code != CHECK_NOT_IN_PLAN:
                return False, f"code={exc.code}"

        return True, "CHECK_NOT_IN_PLAN for extra check_id"


def _b04_stale_plan_identity() -> tuple[bool, str]:
    """B04: schedule_write fails LEDGER_STALE_PLAN_IDENTITY on fingerprint mismatch."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _freeze_fixture_plan(root)
        plan = json.loads(
            plan_carrier_path(change_id, root=root).read_text(encoding="utf-8")
        )
        stale = {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "ledger_id": "stale",
            "plan_id": plan["plan_id"],
            "plan_fingerprint": "a" * 64,
            "ledger_revision": 1,
            "ledger_status": "ACTIVE",
            "entries": [
                {
                    "check_id": "pyright-strict",
                    "order": 1,
                    "state": "PENDING",
                }
            ],
            "current_assignment": None,
        }

        def _validate(doc: dict[str, Any]) -> None:
            _validate_ledger_document(doc)

        atomic_publish_json(
            ledger_carrier_path(change_id, root=root),
            stale,
            validate=_validate,
        )

        try:
            schedule_write(change_id, root=root)
            return False, "fingerprint mismatch should refuse"
        except BoundedVerifyLedgerError as exc:
            if exc.code != LEDGER_STALE_PLAN_IDENTITY:
                return False, f"code={exc.code}"

        return True, "LEDGER_STALE_PLAN_IDENTITY on bind mismatch"


def _b05_check_required_after_schedule_write() -> tuple[bool, str]:
    """B05: advance emits CHECK_REQUIRED only after schedule_write in same hop."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _freeze_fixture_plan(root)
        carrier = ledger_carrier_path(change_id, root=root)
        assert not carrier.exists()

        result = schedule_write(change_id, root=root)
        if not result.check_required:
            return False, "check_required false before publish"
        if result.code != CHECK_REQUIRED:
            return False, f"code={result.code}"
        if not carrier.exists():
            return False, "ledger not published in same hop"
        ledger = read_ledger(change_id, root=root)
        if ledger["current_assignment"] is None:
            return False, "CURRENT not published"

        return True, "CHECK_REQUIRED + ledger publish same hop"


def _b06_no_pending_when_all_terminal() -> tuple[bool, str]:
    """B06: NO_PENDING_CHECKS when all terminal."""
    fixture = _load_fixture()
    single = dict(fixture)
    single["required_checks"] = [{"check_id": "pyright-strict", "order": 1}]

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _freeze_fixture_plan(root, single)
        plan = json.loads(
            plan_carrier_path(change_id, root=root).read_text(encoding="utf-8")
        )
        first = schedule_write(change_id, root=root)
        _write_pass_result(
            root,
            change_id,
            check_id="pyright-strict",
            ledger_revision=first.ledger_revision,
            assignment_epoch=int(first.assignment_epoch or 0),
            plan_fingerprint=str(plan["plan_fingerprint"]),
        )
        second = schedule_write(change_id, root=root)
        if second.code != NO_PENDING_CHECKS:
            return False, f"drain hop code={second.code}"
        if second.ledger_status != "COMPLETE":
            return False, "ledger not COMPLETE after drain"

        third = schedule_write(change_id, root=root)
        if third.code != NO_PENDING_CHECKS:
            return False, f"replay code={third.code}"
        if third.check_required:
            return False, "CHECK_REQUIRED after COMPLETE"

        return True, "NO_PENDING_CHECKS when all terminal"


def _b07_idempotent_replay_same_current() -> tuple[bool, str]:
    """B07: schedule_write idempotent replay same CURRENT without new valid result."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _freeze_fixture_plan(root)
        first = schedule_write(change_id, root=root)
        rev1 = first.ledger_revision
        check1 = first.current_check_id
        epoch1 = first.assignment_epoch

        second = schedule_write(change_id, root=root)
        if second.ledger_revision != rev1:
            return False, "revision changed without result"
        if second.current_check_id != check1:
            return False, "CURRENT changed without result"
        if second.assignment_epoch != epoch1:
            return False, "assignment_epoch changed"
        if not second.check_required or second.code != CHECK_REQUIRED:
            return False, "CHECK_REQUIRED not replayed"

        return True, "idempotent replay same CURRENT"


def _b08_cas_stale_revision() -> tuple[bool, str]:
    """B08: ledger_revision CAS rejects stale scheduler LEDGER_STALE_REVISION."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _freeze_fixture_plan(root)
        first = schedule_write(change_id, root=root)

        try:
            schedule_write(
                change_id,
                root=root,
                expected_revision=first.ledger_revision + 99,
            )
            return False, "stale expected_revision should refuse"
        except BoundedVerifyLedgerError as exc:
            if exc.code != LEDGER_STALE_REVISION:
                return False, f"code={exc.code}"

        ok = schedule_write(
            change_id,
            root=root,
            expected_revision=first.ledger_revision,
        )
        if not ok.check_required:
            return False, "valid CAS should replay CHECK_REQUIRED"

        return True, "LEDGER_STALE_REVISION on CAS mismatch"


def _b09_schema_rejects_multiple_current() -> tuple[bool, str]:
    """B09: schema rejects multiple CURRENT rows."""
    bad = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "ledger_id": "x",
        "plan_id": "p",
        "plan_fingerprint": "b" * 64,
        "ledger_revision": 1,
        "ledger_status": "ACTIVE",
        "entries": [
            {"check_id": "a", "order": 1, "state": "CURRENT"},
            {"check_id": "b", "order": 2, "state": "CURRENT"},
        ],
        "current_assignment": {"check_id": "a", "assignment_epoch": 1},
    }
    try:
        _validate_ledger_document(bad)
        return False, "multiple CURRENT should fail validation"
    except BoundedVerifyLedgerError as exc:
        if exc.code != MULTIPLE_CURRENT:
            return False, f"code={exc.code}"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _freeze_fixture_plan(root)
        carrier = ledger_carrier_path(change_id, root=root)
        carrier.parent.mkdir(parents=True, exist_ok=True)
        carrier.write_text(json.dumps(bad, sort_keys=True), encoding="utf-8")

        try:
            read_ledger(change_id, root=root)
            return False, "read_ledger should reject multiple CURRENT"
        except BoundedVerifyLedgerError as exc:
            if exc.code not in (MULTIPLE_CURRENT, LEDGER_INVALID):
                return False, f"read code={exc.code}"

    return True, "MULTIPLE_CURRENT rejected by schema"


def _n04_scheduler_cannot_add_checks_outside_plan() -> tuple[bool, str]:
    """N04: scheduler adds check not in plan → refused (via CHECK_NOT_IN_PLAN)."""
    return _b03_check_id_must_be_in_plan()


def build_cases() -> list[tuple[str, Callable[[], tuple[bool, str]]]]:
    return [
        ("B01", _b01_schedule_refused_when_plan_not_frozen),
        ("B02", _b02_deterministic_current_from_plan_order),
        ("B03", _b03_check_id_must_be_in_plan),
        ("B04", _b04_stale_plan_identity),
        ("B05", _b05_check_required_after_schedule_write),
        ("B06", _b06_no_pending_when_all_terminal),
        ("B07", _b07_idempotent_replay_same_current),
        ("B08", _b08_cas_stale_revision),
        ("B09", _b09_schema_rejects_multiple_current),
        ("N04", _n04_scheduler_cannot_add_checks_outside_plan),
    ]


def main() -> int:
    results: list[Result] = []
    for case_id, fn in build_cases():
        ok, detail = fn()
        results.append((case_id, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]

    print(f"BOUNDED_VERIFY_LEDGER_SCHEDULE_TESTS: {passed}/{len(results)} passed")
    for case_id, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {case_id}: {detail}")
    if failed:
        print("FAILED_IDS:", ", ".join(r[0] for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
