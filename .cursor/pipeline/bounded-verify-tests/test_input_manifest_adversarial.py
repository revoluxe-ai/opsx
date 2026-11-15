# -*- coding: utf-8 -*-
"""P0-A adversarial fixtures for deterministic input manifest."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import BoundedVerifyPlanError  # noqa: E402
from bounded_verify_snapshot import PLAN_SCHEMA_V2, assert_snapshot_fresh, collect_v2_role_digests  # noqa: E402


def _seed(root: Path, change: str = "adv-change") -> None:
    shutil.copytree(
        REPO_ROOT / "opsx" / "project",
        root / "opsx" / "project",
        dirs_exist_ok=True,
    )
    cdir = root / "openspec" / "changes" / change
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] work\n", encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")
    stroy = root / "src/stroy/notifications/service.py"
    stroy.parent.mkdir(parents=True, exist_ok=True)
    stroy.write_text("v1\n", encoding="utf-8")
    for rel in ("tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    return change


def test_dirty_to_commit_stable_when_content_matches_post_commit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = _seed(root)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
        snap_a = collect_v2_role_digests(change, root=root)["content_digest"]
        path = root / "src/stroy/notifications/service.py"
        path.write_text("v2\n", encoding="utf-8")
        snap_dirty = collect_v2_role_digests(change, root=root)["content_digest"]
        assert snap_dirty != snap_a
        subprocess.run(["git", "add", path], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "v2"], cwd=root, check=True, capture_output=True)
        snap_clean = collect_v2_role_digests(change, root=root)["content_digest"]
        assert snap_clean == snap_dirty


def test_deleted_file_tombstone_drifts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = _seed(root)
        extra = root / "src/stroy/notifications/extra.py"
        extra.write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
        before = collect_v2_role_digests(change, root=root)["content_digest"]
        subprocess.run(["git", "rm", "src/stroy/notifications/extra.py"], cwd=root, check=True, capture_output=True)
        after = collect_v2_role_digests(change, root=root)["content_digest"]
        assert before != after


def test_unrelated_worktree_dirt_ignored() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = _seed(root)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
        before = collect_v2_role_digests(change, root=root)["content_digest"]
        (root / "README-unrelated.md").write_text("noise\n", encoding="utf-8")
        after = collect_v2_role_digests(change, root=root)["content_digest"]
        assert before == after


def test_missing_capability_root_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = _seed(root)
        shutil.rmtree(root / "src/stroy")
        try:
            collect_v2_role_digests(change, root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "TARGET_CAPABILITY_UNRESOLVED"
        else:
            raise AssertionError("expected TARGET_CAPABILITY_UNRESOLVED")


def test_stale_result_after_input_drift() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = _seed(root)
        frozen = collect_v2_role_digests(change, root=root)
        plan = {"schema_version": PLAN_SCHEMA_V2, "implementation_snapshot": frozen}
        assert_snapshot_fresh(plan, change, root=root)
        (root / "src/stroy/notifications/service.py").write_text("drift\n", encoding="utf-8")
        try:
            assert_snapshot_fresh(plan, change, root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "BLOCKED_SNAPSHOT_DRIFT"
        else:
            raise AssertionError("expected BLOCKED_SNAPSHOT_DRIFT")


def main() -> int:
    test_dirty_to_commit_stable_when_content_matches_post_commit()
    test_deleted_file_tombstone_drifts()
    test_unrelated_worktree_dirt_ignored()
    test_missing_capability_root_fail_closed()
    test_stale_result_after_input_drift()
    print("input manifest adversarial tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
