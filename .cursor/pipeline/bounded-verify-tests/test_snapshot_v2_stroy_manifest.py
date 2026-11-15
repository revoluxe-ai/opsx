# -*- coding: utf-8 -*-
"""S4 P0: capabilities-aligned snapshot manifest (src/stroy, not app/)."""

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
from bounded_verify_snapshot import (  # noqa: E402
    PLAN_SCHEMA_V2,
    assert_snapshot_fresh,
    collect_v2_role_digests,
    live_implementation_snapshot,
)


def _seed_capabilities(root: Path) -> None:
    cap_dst = root / "opsx" / "project" / "capabilities.yaml"
    cap_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        REPO_ROOT / "opsx" / "project" / "capabilities.yaml",
        cap_dst,
    )
    _ensure_test_roots(root)


def _ensure_test_roots(root: Path) -> None:
    for rel in ("tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)


def _seed_change(root: Path, change_id: str) -> None:
    cdir = root / "openspec" / "changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] implement notifications\n", encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")


def _stroy_file(root: Path, rel: str = "src/stroy/notifications/service.py") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def notify() -> None:\n    pass\n", encoding="utf-8")
    return path


def test_missing_capabilities_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root, "c1")
        try:
            collect_v2_role_digests("c1", root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "TARGET_CAPABILITY_UNRESOLVED"
        else:
            raise AssertionError("expected TARGET_CAPABILITY_UNRESOLVED")


def test_stroy_modify_without_tasks_checkbox_drifts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_capabilities(root)
        _seed_change(root, "c1")
        _stroy_file(root)
        frozen = live_implementation_snapshot("c1", root=root, plan_schema=PLAN_SCHEMA_V2)
        plan = {"schema_version": PLAN_SCHEMA_V2, "implementation_snapshot": frozen}
        assert_snapshot_fresh(plan, "c1", root=root)

        target = _stroy_file(root)
        target.write_text("def notify() -> None:\n    raise RuntimeError\n", encoding="utf-8")
        try:
            assert_snapshot_fresh(plan, "c1", root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "BLOCKED_SNAPSHOT_DRIFT"
        else:
            raise AssertionError("expected BLOCKED_SNAPSHOT_DRIFT")


def test_new_stroy_file_not_in_tasks_changes_digest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_capabilities(root)
        _seed_change(root, "c1")
        _stroy_file(root)
        before = collect_v2_role_digests("c1", root=root)["content_digest"]

        extra = root / "src/stroy/notifications/new_module.py"
        extra.write_text("x = 1\n", encoding="utf-8")
        after = collect_v2_role_digests("c1", root=root)["content_digest"]
        assert before != after
        manifest = collect_v2_role_digests("c1", root=root)["coverage_manifest"]
        assert "src/stroy/notifications/new_module.py" in manifest["product_paths"]


def test_git_porcelain_tracks_modified_stroy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_capabilities(root)
        _seed_change(root, "c1")
        path = _stroy_file(root)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        snap1 = collect_v2_role_digests("c1", root=root)["content_digest"]
        path.write_text("def notify() -> None:\n    pass  # edit\n", encoding="utf-8")
        snap2 = collect_v2_role_digests("c1", root=root)["content_digest"]
        assert snap1 != snap2


def main() -> int:
    test_missing_capabilities_fail_closed()
    test_stroy_modify_without_tasks_checkbox_drifts()
    test_new_stroy_file_not_in_tasks_changes_digest()
    test_git_porcelain_tracks_modified_stroy()
    print("snapshot v2 stroy manifest tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
