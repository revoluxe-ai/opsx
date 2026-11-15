# -*- coding: utf-8 -*-
"""P0-C: v2 plans require content_digest at ledger drain."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_ledger import BoundedVerifyLedgerError, schedule_write  # noqa: E402
from bounded_verify_plan import draft_plan, freeze_plan  # noqa: E402
from bounded_verify_result import (  # noqa: E402
    ExecutionOutcome,
    ExpectedAssignment,
    RESULT_STALE_SNAPSHOT,
    compute_result_id,
    create_result,
    validate_result_for_drain,
)
from bounded_verify_supersession import publish_successor_plan  # noqa: E402


def _setup_v2(root: Path) -> tuple[str, str]:
    cap = root / "opsx" / "project" / "capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx" / "project" / "capabilities.yaml", cap)
    fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
    change_id = str(fixture["change_id"])
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")
    (root / "src/stroy").mkdir(parents=True, exist_ok=True)
    (root / "src/stroy" / "placeholder.py").write_text("x=1\n", encoding="utf-8")
    for rel in ("tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    from test_impl_seed import seed_hermetic_impl_ok

    seed_hermetic_impl_ok(change_id, root=root)
    registry = FIXTURES / "registry_snapshot.yaml"
    checks = [{"check_id": "pyright-strict", "order": 1}]
    draft_plan(change_id, checks, root=root, registry_path=registry)
    freeze_plan(change_id, root=root, registry_path=registry)
    publish_successor_plan(change_id, checks, root=root, registry_path=registry)
    plan = json.loads((root / "openspec/changes" / change_id / "bounded-verify/plan.json").read_text())
    return change_id, str(plan["plan_fingerprint"])


def test_wrong_content_digest_blocks_drain() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan_fp = _setup_v2(root)
        hop = schedule_write(change_id, expected_revision=1, root=root)
        snap = json.loads(
            (root / "openspec/changes" / change_id / "bounded-verify/plan.json").read_text()
        )["implementation_snapshot"]["content_digest"]
        assignment = ExpectedAssignment(
            check_id="pyright-strict",
            assignment_epoch=int(hop.assignment_epoch or 1),
            ledger_revision=int(hop.ledger_revision),
            plan_fingerprint=plan_fp,
            content_digest=snap,
        )
        result_id = compute_result_id(
            plan_fp,
            assignment.ledger_revision,
            assignment.check_id,
            "exec-1",
        )
        bad = {
            "schema_version": "bounded-verify-result-v1",
            "result_id": result_id,
            "plan_fingerprint": plan_fp,
            "ledger_revision": assignment.ledger_revision,
            "assignment_epoch": assignment.assignment_epoch,
            "check_id": assignment.check_id,
            "execution_id": "exec-1",
            "result_status": "PASS",
            "producer": "test",
            "content_digest": "f" * 64,
        }
        decision = validate_result_for_drain(
            bad,
            ExpectedAssignment(
                check_id=assignment.check_id,
                assignment_epoch=assignment.assignment_epoch,
                ledger_revision=assignment.ledger_revision,
                plan_fingerprint=plan_fp,
                content_digest=snap,
            ),
        )
        assert not decision.allowed
        assert decision.code == RESULT_STALE_SNAPSHOT


def test_missing_digest_on_result_blocks_drain() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, plan_fp = _setup_v2(root)
        hop = schedule_write(change_id, expected_revision=1, root=root)
        snap = json.loads(
            (root / "openspec/changes" / change_id / "bounded-verify/plan.json").read_text()
        )["implementation_snapshot"]["content_digest"]
        assignment = ExpectedAssignment(
            check_id="pyright-strict",
            assignment_epoch=int(hop.assignment_epoch or 1),
            ledger_revision=int(hop.ledger_revision),
            plan_fingerprint=plan_fp,
        )
        create_result(
            change_id,
            assignment,
            ExecutionOutcome(execution_id="exec-missing-digest", result_status="PASS"),
            root=root,
        )
        try:
            schedule_write(change_id, expected_revision=hop.ledger_revision, root=root)
        except BoundedVerifyLedgerError as exc:
            assert exc.code == RESULT_STALE_SNAPSHOT
        else:
            raise AssertionError("drain must fail without content_digest on v2 plan")


def main() -> int:
    test_wrong_content_digest_blocks_drain()
    test_missing_digest_on_result_blocks_drain()
    print("content digest drain tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
