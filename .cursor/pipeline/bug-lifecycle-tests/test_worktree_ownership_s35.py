"""S3.5 worktree ownership reconcile (D2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bug_lifecycle.worktree_ownership import load_ownership_receipt, reconcile_worktree_ownership


def test_per_work_item_receipts_from_delta_map(orch_hermetic_root) -> None:
    root = orch_hermetic_root
    mission = "ORCH-wt"
    work_items = [
        {
            "finding_id": "F-1",
            "work_item_id": "WI-1",
            "location": "src/store/index.js",
            "route": "MUST_FIX_REPAIR",
        },
        {
            "finding_id": "F-2",
            "work_item_id": "WI-2",
            "location": "src/components/views/BankView.vue",
            "route": "MUST_FIX_REPAIR",
        },
    ]
    delta = {
        "src/store/index.js": {"work_item_id": "WI-1"},
        "src/components/views/BankView.vue": {"work_item_id": "WI-2"},
    }
    result = reconcile_worktree_ownership(
        root,
        mission_id=mission,
        execution_id="exec-wt",
        work_items=work_items,
        delta_map=delta,
    )
    assert result["STATUS"] == "WORKTREE_OWNERSHIP_BOUND"
    r1 = load_ownership_receipt(root, mission, "WI-2")
    assert r1 is not None
    assert r1["FORBIDDEN_PATHS"] == ["src/store/index.js"]
    assert r1["WORK_ITEM_SCOPE_PATHS"] == ["src/components/views/BankView.vue"]


def test_ambiguous_dirty_path_blocks(orch_hermetic_root) -> None:
    root = orch_hermetic_root
    work_items = [
        {
            "finding_id": "F-1",
            "work_item_id": "WI-1",
            "location": "src/shared.js",
            "route": "MUST_FIX_REPAIR",
        },
        {
            "finding_id": "F-2",
            "work_item_id": "WI-2",
            "location": "src/shared.js",
            "route": "MUST_FIX_REPAIR",
        },
    ]
    result = reconcile_worktree_ownership(
        root,
        mission_id="ORCH-amb",
        execution_id="exec-amb",
        work_items=work_items,
        dirty_paths=["src/shared.js"],
    )
    assert result["STATUS"] == "BLOCKED_WORKTREE_OWNERSHIP"


def test_git_dirty_unmapped_path_blocks(tmp_path: Path) -> None:
    root = tmp_path / "gitrepo"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    app = root / "app"
    app.mkdir()
    target = app / "dirty.py"
    target.write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "app/dirty.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    target.write_text("b\n", encoding="utf-8")
    work_items = [
        {
            "finding_id": "F-g",
            "work_item_id": "WI-g",
            "location": "app/other.py:1",
            "route": "MUST_FIX_REPAIR",
        }
    ]
    result = reconcile_worktree_ownership(
        root,
        mission_id="ORCH-git",
        execution_id="exec-git",
        work_items=work_items,
        delta_map={"app/other.py": {"work_item_id": "WI-g"}},
    )
    assert result["STATUS"] == "BLOCKED_WORKTREE_OWNERSHIP"
    assert result.get("REASON") == "unmapped_git_dirty_path"
