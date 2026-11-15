# -*- coding: utf-8 -*-
"""SLICE-001 behavioral tests (A01–A06) for bounded verification plan freeze.

Покрывает INDIVIDUAL_ACQUISITION_A: sole writer API, fingerprint, immutability,
отсутствие GO/NO_GO при freeze, registry validation и atomic publication.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(BV_ROOT))

from bounded_verify_atomic import (  # noqa: E402
    atomic_publish_json,
    is_canonical_plan_readable,
    read_canonical_json,
)
from bounded_verify_plan import (  # noqa: E402
    PLAN_FROZEN_MUTATION_REFUSED,
    REGISTRY_CHECK_UNKNOWN,
    BoundedVerifyPlanError,
    assert_plan_mutable,
    compute_plan_fingerprint,
    draft_plan,
    freeze_plan,
    plan_carrier_path,
    read_plan,
)

Result = tuple[str, bool, str]


def _load_fixture() -> dict[str, Any]:
    return json.loads(
        (FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8")
    )


def _setup_change_tree(root: Path, fixture: dict[str, Any]) -> Path:
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
    return change_dir


def _registry_path() -> Path:
    return FIXTURES / "registry_snapshot.yaml"


def _a01_only_module_api_writes_plan() -> tuple[bool, str]:
    """A01: только bounded_verify_plan API пишет plan.json."""
    fixture = _load_fixture()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_change_tree(root, fixture)
        change_id = str(fixture["change_id"])
        carrier = plan_carrier_path(change_id, root=root)
        assert not carrier.exists()

        checks = list(fixture["required_checks"])
        draft_plan(
            change_id,
            checks,
            root=root,
            registry_path=_registry_path(),
        )
        assert carrier.exists()

        # Прямая запись в plan.json не является supported mutation path;
        # read_plan после freeze от API остаётся единственным каноническим читателем.
        frozen = freeze_plan(
            change_id,
            root=root,
            registry_path=_registry_path(),
        )
        raw = json.loads(carrier.read_text(encoding="utf-8"))
        assert raw["plan_status"] == "FROZEN"
        assert frozen.plan_fingerprint == raw["plan_fingerprint"]

        try:
            draft_plan(
                change_id,
                checks,
                root=root,
                registry_path=_registry_path(),
            )
            return False, "draft_plan after freeze should refuse"
        except BoundedVerifyPlanError as exc:
            if exc.code != PLAN_FROZEN_MUTATION_REFUSED:
                return False, f"unexpected code {exc.code}"

        return True, "module API sole supported writer; post-freeze refused"


def _a02_freeze_sets_frozen_and_stable_fingerprint() -> tuple[bool, str]:
    """A02: freeze → FROZEN + стабильный plan_fingerprint (не git HEAD)."""
    fixture = _load_fixture()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_change_tree(root, fixture)
        change_id = str(fixture["change_id"])
        checks = list(fixture["required_checks"])

        draft_plan(
            change_id,
            checks,
            root=root,
            registry_path=_registry_path(),
        )
        first = freeze_plan(
            change_id,
            root=root,
            registry_path=_registry_path(),
        )
        second = freeze_plan(
            change_id,
            root=root,
            registry_path=_registry_path(),
        )

        plan = read_plan(change_id, root=root)
        if plan["plan_status"] != "FROZEN":
            return False, "plan_status not FROZEN"
        if not plan["plan_fingerprint"]:
            return False, "missing plan_fingerprint"
        if first.plan_fingerprint != second.plan_fingerprint:
            return False, "fingerprint unstable across idempotent freeze"
        if "git" in plan["plan_fingerprint"].lower():
            return False, "fingerprint must not embed git HEAD"

        subset = {
            "schema_version": plan["schema_version"],
            "change_id": plan["change_id"],
            "plan_id": plan["plan_id"],
            "plan_status": plan["plan_status"],
            "required_checks": plan["required_checks"],
            "implementation_snapshot": plan["implementation_snapshot"],
        }
        expected = compute_plan_fingerprint(subset)
        if expected != plan["plan_fingerprint"]:
            return False, "fingerprint mismatch vs canonical subset"

        return True, f"fingerprint={plan['plan_fingerprint'][:16]}..."


def _a03_post_freeze_mutation_refused() -> tuple[bool, str]:
    """A03: мутация FROZEN plan через API → PLAN_FROZEN_MUTATION_REFUSED."""
    fixture = _load_fixture()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_change_tree(root, fixture)
        change_id = str(fixture["change_id"])
        checks = list(fixture["required_checks"])

        draft_plan(
            change_id,
            checks,
            root=root,
            registry_path=_registry_path(),
        )
        freeze_plan(change_id, root=root, registry_path=_registry_path())

        for fn, label in (
            (lambda: assert_plan_mutable(change_id, root=root), "assert_plan_mutable"),
            (
                lambda: draft_plan(
                    change_id,
                    checks,
                    root=root,
                    registry_path=_registry_path(),
                ),
                "draft_plan",
            ),
        ):
            try:
                fn()
                return False, f"{label} should refuse frozen plan"
            except BoundedVerifyPlanError as exc:
                if exc.code != PLAN_FROZEN_MUTATION_REFUSED:
                    return False, f"{label} code={exc.code}"

        return True, "all post-freeze mutations refused"


def _a04_freeze_does_not_execute_checks() -> tuple[bool, str]:
    """A04: freeze не запускает checks и не эмитит GO/NO_GO."""
    fixture = _load_fixture()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_change_tree(root, fixture)
        change_id = str(fixture["change_id"])

        executed: list[str] = []

        def _fake_run_check(check_id: str) -> None:
            executed.append(check_id)

        draft_plan(
            change_id,
            list(fixture["required_checks"]),
            root=root,
            registry_path=_registry_path(),
        )
        freeze_plan(
            change_id,
            root=root,
            registry_path=_registry_path(),
            check_executor=_fake_run_check,
        )

        if executed:
            return False, f"checks executed during freeze: {executed}"

        carrier = plan_carrier_path(change_id, root=root)
        text = carrier.read_text(encoding="utf-8").upper()
        for token in ("GO", "NO_GO", "AGGREGATE_PASS"):
            if token in text:
                return False, f"freeze output contains {token}"

        return True, "no check execution or GO/NO_GO emission"


def _a05_unknown_check_refused() -> tuple[bool, str]:
    """A05: неизвестный check_id → REGISTRY_CHECK_UNKNOWN."""
    fixture = _load_fixture()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_change_tree(root, fixture)
        change_id = str(fixture["change_id"])
        bad_checks = [
            {"check_id": str(fixture["unknown_check_id"]), "order": 1},
        ]

        try:
            draft_plan(
                change_id,
                bad_checks,
                root=root,
                registry_path=_registry_path(),
            )
            return False, "draft_plan should refuse unknown check_id"
        except BoundedVerifyPlanError as exc:
            if exc.code != REGISTRY_CHECK_UNKNOWN:
                return False, f"code={exc.code}"

        return True, "unknown check_id refused at draft"


def _a06_new_plan_requires_new_plan_id_no_overwrite() -> tuple[bool, str]:
    """A06: новый plan = новый plan_id; нет overwrite-frozen escape."""
    fixture = _load_fixture()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _setup_change_tree(root, fixture)
        change_id = str(fixture["change_id"])
        checks = list(fixture["required_checks"])

        draft_plan(
            change_id,
            checks,
            root=root,
            registry_path=_registry_path(),
        )
        frozen = freeze_plan(
            change_id,
            root=root,
            registry_path=_registry_path(),
        )
        plan_id_p1 = frozen.plan_id

        # Нет force/regenerate API в модуле.
        plan_mod = BV_ROOT / "bounded_verify_plan.py"
        mod_text = plan_mod.read_text(encoding="utf-8")
        for escape in ("--force", "overwrite-frozen", "REGENERATE", "force_freeze"):
            if escape in mod_text:
                return False, f"force escape token present: {escape}"

        try:
            draft_plan(
                change_id,
                checks,
                root=root,
                registry_path=_registry_path(),
            )
            return False, "overwrite frozen plan path should be refused"
        except BoundedVerifyPlanError as exc:
            if exc.code != PLAN_FROZEN_MUTATION_REFUSED:
                return False, f"code={exc.code}"

        plan_after = read_plan(change_id, root=root)
        if plan_after["plan_id"] != plan_id_p1:
            return False, "frozen plan_id changed without authorized new publication"

        return True, "frozen plan immutable; no force escape in module"


def _at_partial_temp_not_canonical() -> tuple[bool, str]:
    """Atomic: partial temp sibling не читается как canonical plan."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan_path = root / "plan.json"
        partial = root / "plan.json.tmp.12345.abc"
        partial.write_text('{"plan_status": "FROZEN", "incomplete":', encoding="utf-8")

        if is_canonical_plan_readable(plan_path):
            return False, "absent canonical should not be readable"
        if read_canonical_json(plan_path) is not None:
            return False, "absent canonical should return None"

        return True, "partial temp ignored; absent canonical not readable"


def _at_crash_before_replace_preserves_prior() -> tuple[bool, str]:
    """Atomic: crash до os.replace сохраняет prior canonical state."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        plan_path = root / "plan.json"
        prior = {
            "schema_version": "bounded-verify-plan-v1",
            "change_id": "c1",
            "plan_id": "p-prior",
            "plan_status": "DRAFT",
            "plan_fingerprint": "",
            "required_checks": [],
            "implementation_snapshot": {"content_digest": "abc"},
        }
        plan_path.write_text(
            json.dumps(prior, sort_keys=True), encoding="utf-8"
        )

        new_payload = dict(prior)
        new_payload["plan_status"] = "FROZEN"
        new_payload["plan_fingerprint"] = "deadbeef"

        def _validate(doc: dict[str, Any]) -> None:
            if doc.get("plan_status") != "FROZEN":
                raise ValueError("not frozen")

        atomic_publish_json(
            plan_path,
            new_payload,
            validate=_validate,
            test_hook={"crash_before_replace": True},
        )

        after = json.loads(plan_path.read_text(encoding="utf-8"))
        if after["plan_status"] != "DRAFT":
            return False, "prior DRAFT plan mutated after crash-before-replace"
        if (root / "plan.json.tmp").exists():
            pass  # temp may remain; readers ignore it

        return True, "prior canonical preserved on crash before replace"


def _at_malformed_canonical_not_valid_frozen() -> tuple[bool, str]:
    """Atomic: malformed canonical plan не принимается как valid FROZEN."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "malformed-change"
        fixture = _load_fixture()
        fixture = dict(fixture)
        fixture["change_id"] = change_id
        _setup_change_tree(root, fixture)

        carrier = plan_carrier_path(change_id, root=root)
        carrier.parent.mkdir(parents=True, exist_ok=True)
        carrier.write_text("{not valid json", encoding="utf-8")

        try:
            read_plan(change_id, root=root)
            return False, "malformed plan should not parse as valid"
        except BoundedVerifyPlanError:
            pass

        carrier.write_text(
            json.dumps(
                {
                    "schema_version": "bounded-verify-plan-v1",
                    "change_id": change_id,
                    "plan_id": "p1",
                    "plan_status": "FROZEN",
                    "plan_fingerprint": "",
                    "required_checks": [],
                    "implementation_snapshot": {"content_digest": "x"},
                }
            ),
            encoding="utf-8",
        )
        try:
            plan = read_plan(change_id, root=root)
            if plan["plan_status"] == "FROZEN" and not plan["plan_fingerprint"]:
                return False, "FROZEN without fingerprint accepted"
        except BoundedVerifyPlanError:
            pass

        return True, "malformed/partial canonical rejected"


def build_cases() -> list[tuple[str, Callable[[], tuple[bool, str]]]]:
    return [
        ("A01", _a01_only_module_api_writes_plan),
        ("A02", _a02_freeze_sets_frozen_and_stable_fingerprint),
        ("A03", _a03_post_freeze_mutation_refused),
        ("A04", _a04_freeze_does_not_execute_checks),
        ("A05", _a05_unknown_check_refused),
        ("A06", _a06_new_plan_requires_new_plan_id_no_overwrite),
        ("AT_PARTIAL_TEMP", _at_partial_temp_not_canonical),
        ("AT_CRASH_REPLACE", _at_crash_before_replace_preserves_prior),
        ("AT_MALFORMED_CANONICAL", _at_malformed_canonical_not_valid_frozen),
    ]


def main() -> int:
    results: list[Result] = []
    for case_id, fn in build_cases():
        ok, detail = fn()
        results.append((case_id, ok, detail))

    passed = sum(1 for _, ok, _ in results if ok)
    failed = [r for r in results if not r[1]]

    print(f"BOUNDED_VERIFY_PLAN_FREEZE_TESTS: {passed}/{len(results)} passed")
    for case_id, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {case_id}: {detail}")
    if failed:
        print("FAILED_IDS:", ", ".join(r[0] for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
