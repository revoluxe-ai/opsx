# -*- coding: utf-8 -*-
"""P0-C supersession crash injection + idempotent recovery."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import draft_plan, freeze_plan  # noqa: E402
from bounded_verify_supersession import (  # noqa: E402
    generation_path,
    pending_path,
    publish_successor_plan,
)


def _setup(root: Path) -> str:
    shutil.copytree(REPO_ROOT / "opsx/project", root / "opsx/project", dirs_exist_ok=True)
    fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
    change_id = str(fixture["change_id"])
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")
    (root / "src/stroy").mkdir(parents=True, exist_ok=True)
    (root / "src/stroy/placeholder.py").write_text("x=1\n", encoding="utf-8")
    for rel in ("tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    return change_id


def test_crash_before_plan_then_resume() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup(root)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "pyright-strict", "order": 1}]
        draft_plan(change_id, checks, root=root, registry_path=registry)
        freeze_plan(change_id, root=root, registry_path=registry)
        os.environ["BOUNDED_VERIFY_SUPERSESSION_CRASH"] = "before_plan"
        try:
            publish_successor_plan(change_id, checks, root=root, registry_path=registry)
        except RuntimeError:
            pass
        finally:
            os.environ.pop("BOUNDED_VERIFY_SUPERSESSION_CRASH", None)
        assert pending_path(change_id, root=root).is_file()
        result = publish_successor_plan(change_id, checks, root=root, registry_path=registry)
        assert not result.noop
        assert generation_path(change_id, root=root).is_file()


def main() -> int:
    test_crash_before_plan_then_resume()
    print("supersession crash recovery tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
