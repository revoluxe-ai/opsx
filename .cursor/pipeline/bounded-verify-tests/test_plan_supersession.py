# -*- coding: utf-8 -*-
"""S4: FROZEN plan supersession — archive immutable + new plan_id."""

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

from bounded_verify_plan import draft_plan, freeze_plan, read_plan  # noqa: E402
from bounded_verify_supersession import (  # noqa: E402
    current_plan_pointer_path,
    publish_successor_plan,
)


def _setup(root: Path) -> str:
    cap = root / "opsx" / "project" / "capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx" / "project" / "capabilities.yaml", cap)
    fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
    change_id = str(fixture["change_id"])
    cdir = root / "openspec" / "changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")
    (root / "src/stroy").mkdir(parents=True, exist_ok=True)
    (root / "src/stroy" / "placeholder.py").write_text("x=1\n", encoding="utf-8")
    for rel in ("tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    from test_impl_seed import seed_hermetic_impl_ok

    seed_hermetic_impl_ok(change_id, root=root)
    return change_id


def test_successor_archives_and_rejects_old_result() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup(root)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "pyright-strict", "order": 1}]
        draft_plan(change_id, checks, root=root, registry_path=registry)
        frozen = freeze_plan(change_id, root=root, registry_path=registry)
        old_fp = frozen.plan_fingerprint

        successor = publish_successor_plan(change_id, checks, root=root, registry_path=registry)
        assert not successor.noop
        assert successor.new_plan_id != successor.old_plan_id

        archive_plan = (
            root
            / "openspec/changes"
            / change_id
            / "bounded-verify/archive"
            / successor.old_plan_id
            / "plan.json"
        )
        assert archive_plan.is_file()
        archived = json.loads(archive_plan.read_text(encoding="utf-8"))
        assert archived["plan_id"] == successor.old_plan_id
        assert archived["plan_status"] == "FROZEN"

        current = json.loads(current_plan_pointer_path(change_id, root=root).read_text())
        assert current["plan_id"] == successor.new_plan_id

        new_plan = read_plan(change_id, root=root)
        assert new_plan["schema_version"] == "bounded-verify-plan-v2"
        assert str(new_plan["plan_fingerprint"]) != old_fp
        assert str(new_plan["plan_id"]) != successor.old_plan_id


def main() -> int:
    test_successor_archives_and_rejects_old_result()
    print("plan supersession tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
