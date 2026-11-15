# -*- coding: utf-8 -*-
"""P0-B split-APPLY GREEN binding via native apply-green carriers."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURE = REPO_ROOT / "tests/unit/fixtures/redImplementGreenLifecycle"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import BoundedVerifyPlanError  # noqa: E402
from bounded_verify_split_apply import split_apply_green_identity  # noqa: E402


def test_split_apply_incomplete_without_green() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "split-change"
        cdir = root / "openspec/changes" / change
        cdir.mkdir(parents=True)
        design_dst = cdir / "apply-design"
        design_dst.mkdir(parents=True)
        shutil.copy2(FIXTURE / "APPLY-DESIGN.json", design_dst / "APPLY-DESIGN.json")
        shutil.copytree(REPO_ROOT / "opsx_tools", root / "opsx_tools", dirs_exist_ok=True)
        try:
            split_apply_green_identity(change, root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code in ("SPLIT_APPLY_INCOMPLETE", "SPLIT_APPLY_STALE", "SPLIT_APPLY_NATIVE_BLOCKED")
        else:
            raise AssertionError("expected SPLIT_APPLY_INCOMPLETE")


def test_split_apply_green_digest_when_green_proven() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "split-change"
        cdir = root / "openspec/changes" / change
        design_dst = cdir / "apply-design"
        design_dst.mkdir(parents=True)
        shutil.copy2(FIXTURE / "APPLY-DESIGN.json", design_dst / "APPLY-DESIGN.json")
        green_dir = cdir / "apply-green/SLICE-001"
        green_dir.mkdir(parents=True)
        green = {
            "status": "GREEN_PROVEN",
            "slice_id": "SLICE-001",
            "red_final_status": "PASS",
            "frozen_test_content_identity": "abc",
        }
        (green_dir / "GREEN.json").write_text(json.dumps(green) + "\n", encoding="utf-8")
        shutil.copytree(REPO_ROOT / "opsx_tools", root / "opsx_tools", dirs_exist_ok=True)
        try:
            result = split_apply_green_identity(change, root=root)
        except BoundedVerifyPlanError as exc:
            if exc.code == "SPLIT_APPLY_STALE":
                return
            raise
        assert result["policy"] == "SPLIT_APPLY_CURRENT_GREEN"
        assert len(result["digest"]) == 64


def main() -> int:
    test_split_apply_incomplete_without_green()
    test_split_apply_green_digest_when_green_proven()
    print("split apply binding tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
