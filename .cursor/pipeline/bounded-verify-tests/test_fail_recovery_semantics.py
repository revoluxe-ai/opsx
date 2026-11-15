# -*- coding: utf-8 -*-
"""P0: FAIL recovery — no PASS-wins over immutable FAIL for same assignment."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_executor import run_current_check  # noqa: E402
from bounded_verify_ledger import schedule_write  # noqa: E402
from bounded_verify_plan import draft_plan, freeze_plan  # noqa: E402
from bounded_verify_ledger import BoundedVerifyLedgerError  # noqa: E402
from bounded_verify_recovery import (  # noqa: E402
    RECOVERY_INPUT_DRIFT,
    authorize_check_recovery,
)
from bounded_verify_result import (  # noqa: E402
    BoundedVerifyResultError,
    ExecutionOutcome,
    ExpectedAssignment,
    RESULT_ASSIGNMENT_ALREADY_BOUND,
    create_result,
    find_results_for_assignment,
)


def _pass_runner(check_id: str, _r: Path, _root: Path):
    return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS"}


def _fail_runner(check_id: str, _r: Path, _root: Path):
    return "FAIL", {"CHECK_ID": check_id, "STATUS": "FAIL"}


def _setup(root: Path) -> str:
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
    return change_id


def test_fail_wins_when_pass_and_fail_same_assignment() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup(root)
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
        create_result(
            change_id,
            assignment,
            ExecutionOutcome(execution_id="exec-fail", result_status="FAIL"),
            root=root,
        )
        try:
            create_result(
                change_id,
                assignment,
                ExecutionOutcome(execution_id="exec-pass", result_status="PASS"),
                root=root,
            )
        except BoundedVerifyResultError as exc:
            assert exc.code == RESULT_ASSIGNMENT_ALREADY_BOUND
        else:
            raise AssertionError("second result for same assignment must be refused")
        hop2 = schedule_write(change_id, expected_revision=hop.ledger_revision, root=root)
        assert hop2.code == "VERIFICATION_CHECK_FAILED"
        assert len(find_results_for_assignment(change_id, assignment, root=root)) == 1


def test_explicit_recovery_allows_retry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup(root)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "pyright-strict", "order": 1}]
        draft_plan(change_id, checks, root=root, registry_path=registry)
        plan = freeze_plan(change_id, root=root, registry_path=registry)
        hop = schedule_write(change_id, expected_revision=1, root=root)
        run_current_check(
            change_id,
            check_id=hop.current_check_id,
            assignment_epoch=hop.assignment_epoch,
            ledger_revision=hop.ledger_revision,
            execution_id=str(uuid.uuid4()),
            root=root,
            registry_path=registry,
            check_runner=_fail_runner,
        )
        fail_hop = schedule_write(change_id, expected_revision=hop.ledger_revision, root=root)
        assert fail_hop.code == "VERIFICATION_CHECK_FAILED"
        authorize_check_recovery(
            change_id,
            "pyright-strict",
            causal_finding="TECHNICAL_RETRY: transient runner flake after fix",
            technical_retry=True,
            root=root,
        )
        retry_hop = schedule_write(change_id, root=root)
        assert retry_hop.check_required is True
        run_current_check(
            change_id,
            check_id=retry_hop.current_check_id,
            assignment_epoch=retry_hop.assignment_epoch,
            ledger_revision=retry_hop.ledger_revision,
            execution_id=str(uuid.uuid4()),
            root=root,
            registry_path=registry,
            check_runner=_pass_runner,
        )
        pass_hop = schedule_write(change_id, expected_revision=retry_hop.ledger_revision, root=root)
        assert pass_hop.ledger_status == "COMPLETE"


def test_recovery_rejects_unchanged_inputs_without_technical_retry() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup(root)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "pyright-strict", "order": 1}]
        draft_plan(change_id, checks, root=root, registry_path=registry)
        freeze_plan(change_id, root=root, registry_path=registry)
        hop = schedule_write(change_id, expected_revision=1, root=root)
        run_current_check(
            change_id,
            check_id=hop.current_check_id,
            assignment_epoch=hop.assignment_epoch,
            ledger_revision=hop.ledger_revision,
            execution_id=str(uuid.uuid4()),
            root=root,
            registry_path=registry,
            check_runner=_fail_runner,
        )
        schedule_write(change_id, expected_revision=hop.ledger_revision, root=root)
        try:
            authorize_check_recovery(
                change_id,
                "pyright-strict",
                causal_finding="spurious retry without TECHNICAL_RETRY policy",
                root=root,
            )
        except BoundedVerifyLedgerError as exc:
            assert exc.code == RECOVERY_INPUT_DRIFT
        else:
            raise AssertionError("recovery must require input change or TECHNICAL_RETRY")


def main() -> int:
    test_fail_wins_when_pass_and_fail_same_assignment()
    test_explicit_recovery_allows_retry()
    test_recovery_rejects_unchanged_inputs_without_technical_retry()
    print("fail recovery semantics tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
