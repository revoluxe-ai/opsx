# -*- coding: utf-8 -*-
"""P0-B: concurrent result writers — exactly one assignment binding."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_ledger import schedule_write  # noqa: E402
from bounded_verify_plan import draft_plan, freeze_plan  # noqa: E402
from bounded_verify_result import (  # noqa: E402
    BoundedVerifyResultError,
    ExecutionOutcome,
    ExpectedAssignment,
    RESULT_ASSIGNMENT_ALREADY_BOUND,
    create_result,
    find_result_for_current,
)


def _setup(root: Path) -> tuple[str, ExpectedAssignment]:
    shutil.copytree(REPO_ROOT / "opsx/project", root / "opsx/project", dirs_exist_ok=True)
    fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
    change_id = str(fixture["change_id"])
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
    for rel in ("src/stroy", "tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    from test_impl_seed import seed_hermetic_impl_ok

    seed_hermetic_impl_ok(change_id, root=root)
    registry = FIXTURES / "registry_snapshot.yaml"
    checks = [{"check_id": "pyright-strict", "order": 1}]
    draft_plan(change_id, checks, root=root, registry_path=registry)
    plan = freeze_plan(change_id, root=root, registry_path=registry)
    hop = schedule_write(change_id, expected_revision=1, root=root)
    assignment = ExpectedAssignment(
        check_id="pyright-strict",
        assignment_epoch=int(hop.assignment_epoch or 1),
        ledger_revision=int(hop.ledger_revision),
        plan_fingerprint=plan.plan_fingerprint,
    )
    return change_id, assignment


def test_concurrent_writers_one_authoritative_binding() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, assignment = _setup(root)
        outcomes: list[str] = []
        errors: list[BoundedVerifyResultError] = []
        lock = threading.Lock()

        def writer(exec_suffix: str) -> None:
            try:
                create_result(
                    change_id,
                    assignment,
                    ExecutionOutcome(
                        execution_id=f"exec-{exec_suffix}",
                        result_status="PASS",
                    ),
                    root=root,
                )
                with lock:
                    outcomes.append(exec_suffix)
            except BoundedVerifyResultError as exc:
                with lock:
                    errors.append(exc)

        t1 = threading.Thread(target=writer, args=("a",))
        t2 = threading.Thread(target=writer, args=("b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(outcomes) == 1
        assert len(errors) == 1
        assert errors[0].code == RESULT_ASSIGNMENT_ALREADY_BOUND
        authoritative = find_result_for_current(change_id, assignment, root=root)
        assert authoritative is not None
        assert str(authoritative.get("execution_id")) == f"exec-{outcomes[0]}"


def main() -> int:
    test_concurrent_writers_one_authoritative_binding()
    print("concurrent result writers tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
