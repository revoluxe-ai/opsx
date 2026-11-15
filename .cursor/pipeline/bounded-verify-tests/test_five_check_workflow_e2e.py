# -*- coding: utf-8 -*-
"""S6: five-check workflow via executor → result writer → ledger reconcile."""

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
from bounded_verify_result import read_result  # noqa: E402

CHECK_IDS = ("pyright-strict", "mypy", "check-a", "check-b", "check-c")


def _pass_runner(check_id: str, _registry: Path, _root: Path):
    return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS", "EXIT_CODE": 0}


def _fail_runner(check_id: str, _registry: Path, _root: Path):
    if check_id == CHECK_IDS[0]:
        return "FAIL", {"CHECK_ID": check_id, "STATUS": "FAIL", "EXIT_CODE": 1}
    return "PASS", {"CHECK_ID": check_id, "STATUS": "PASS", "EXIT_CODE": 0}


def _registry_with_five(root: Path) -> Path:
    text = (
        "version: 1\n"
        "default_launch: SERIAL_ORCHESTRATOR\n"
        "modes:\n  FAST:\n    waves:\n      - id: A\n        checks: [pyright-strict, mypy, check-a, check-b, check-c]\n"
        "checks:\n"
    )
    for cid in CHECK_IDS:
        text += (
            f"  {cid}:\n"
            f"    task_type: shell\n"
            f"    pack: .cursor/static-check/checks/{cid}\n"
        )
    path = root / "registry_five.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _seed(root: Path) -> str:
    cap = root / "opsx" / "project" / "capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx" / "project" / "capabilities.yaml", cap)
    fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
    change_id = str(fixture["change_id"])
    cdir = root / "openspec" / "changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
    from test_impl_seed import seed_hermetic_impl_ok

    seed_hermetic_impl_ok(change_id, root=root)
    return change_id


def _checks():
    return [{"check_id": cid, "order": i + 1} for i, cid in enumerate(CHECK_IDS)]


def _run_through(root: Path, runner):
    change_id = _seed(root)
    registry = _registry_with_five(root)
    draft_plan(change_id, _checks(), root=root, registry_path=registry)
    freeze_plan(change_id, root=root, registry_path=registry)
    hop = schedule_write(change_id, expected_revision=1, root=root)
    completed = 0
    while hop.check_required and hop.current_check_id:
        exec_id = str(uuid.uuid4())
        run_current_check(
            change_id,
            check_id=hop.current_check_id,
            assignment_epoch=hop.assignment_epoch,
            ledger_revision=hop.ledger_revision,
            execution_id=exec_id,
            root=root,
            registry_path=registry,
            check_runner=runner,
        )
        completed += 1
        hop = schedule_write(change_id, expected_revision=hop.ledger_revision, root=root)
        if runner == _fail_runner and completed >= 1:
            break
    return change_id, completed, hop


def test_all_five_pass_via_executor_chain() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id, completed, hop = _run_through(root, _pass_runner)
        assert hop.ledger_status == "COMPLETE"
        assert completed == 5
        rdir = root / "openspec/changes" / change_id / "bounded-verify/results"
        results = list(rdir.glob("*.json"))
        assert len(results) == 5
        sample = read_result(change_id, results[0].stem, root=root)
        assert sample["producer"] == "bounded_verify_executor"
        assert "content_digest" in sample


def test_first_fail_stops_frontier() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _change_id, completed, hop = _run_through(root, _fail_runner)
        assert completed == 1
        assert hop.ledger_status != "COMPLETE"


def main() -> int:
    test_all_five_pass_via_executor_chain()
    test_first_fail_stops_frontier()
    print("five-check workflow e2e OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
