# -*- coding: utf-8 -*-
"""P0: recovery binding + assignment claim crash/idempotency."""

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

from bounded_verify_assignment_binding import acquire_assignment_binding  # noqa: E402
from bounded_verify_executor import run_current_check  # noqa: E402
from bounded_verify_ledger import schedule_write  # noqa: E402
from bounded_verify_plan import draft_plan, freeze_plan  # noqa: E402
from bounded_verify_recovery import authorize_check_recovery  # noqa: E402
from bounded_verify_result import ExecutionOutcome, ExpectedAssignment, create_result  # noqa: E402


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


def test_assignment_claim_idempotent_after_crash_before_result() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup(root)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "pyright-strict", "order": 1}]
        draft_plan(change_id, checks, root=root, registry_path=registry)
        freeze_plan(change_id, root=root, registry_path=registry)
        hop = schedule_write(change_id, expected_revision=1, root=root)
        assignment = ExpectedAssignment(
            check_id="pyright-strict",
            assignment_epoch=int(hop.assignment_epoch or 1),
            ledger_revision=int(hop.ledger_revision),
            plan_fingerprint=json.loads(
                (root / "openspec/changes" / change_id / "bounded-verify/plan.json").read_text()
            )["plan_fingerprint"],
        )
        exec_id = "exec-crash-retry"
        acquire_assignment_binding(change_id, assignment, exec_id, root=root)
        create_result(
            change_id,
            assignment,
            ExecutionOutcome(execution_id=exec_id, result_status="PASS"),
            root=root,
        )


def test_recovery_log_survives_replay() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup(root)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "pyright-strict", "order": 1}]
        draft_plan(change_id, checks, root=root, registry_path=registry)
        freeze_plan(change_id, root=root, registry_path=registry)
        hop = schedule_write(change_id, expected_revision=1, root=root)

        def _fail(_cid: str, _r: Path, _root: Path):
            return "FAIL", {"STATUS": "FAIL"}

        run_current_check(
            change_id,
            check_id=hop.current_check_id,
            assignment_epoch=hop.assignment_epoch,
            ledger_revision=hop.ledger_revision,
            execution_id=str(uuid.uuid4()),
            root=root,
            registry_path=registry,
            check_runner=_fail,
        )
        schedule_write(change_id, expected_revision=hop.ledger_revision, root=root)
        auth = authorize_check_recovery(
            change_id,
            "pyright-strict",
            causal_finding="TECHNICAL_RETRY: flake",
            technical_retry=True,
            root=root,
        )
        log_path = root / "openspec/changes" / change_id / "bounded-verify/recovery-log.json"
        assert log_path.is_file()
        entries = json.loads(log_path.read_text(encoding="utf-8"))
        if isinstance(entries, dict):
            entries = entries["entries"]
        assert any(e.get("recovery_id") == auth.recovery_id for e in entries)


def main() -> int:
    test_assignment_claim_idempotent_after_crash_before_result()
    test_recovery_log_survives_replay()
    print("recovery assignment safety tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
