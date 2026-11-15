# -*- coding: utf-8 -*-
"""P0: native vs bounded STALE_GREEN parity."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURE = REPO_ROOT / "tests/unit/fixtures/redImplementGreenLifecycle"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import BoundedVerifyPlanError  # noqa: E402
from bounded_verify_split_apply import split_apply_green_identity  # noqa: E402


def _node_report(root: Path, change: str) -> dict:
    script = root / "opsx_tools/opsx-bounded-verify-green-freshness.mjs"
    proc = subprocess.run(
        ["node", str(script), "--change-root", f"openspec/changes/{change}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def test_stale_green_blocked_in_bounded_when_upstream_missing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "parity-change"
        cdir = root / "openspec/changes" / change
        design_dst = cdir / "apply-design"
        design_dst.mkdir(parents=True)
        shutil.copy2(FIXTURE / "APPLY-DESIGN.json", design_dst / "APPLY-DESIGN.json")
        green_dir = cdir / "apply-green/SLICE-001"
        green_dir.mkdir(parents=True)
        green = {
            "status": "GREEN_PROVEN",
            "slice_id": "SLICE-001",
            "apply_design_identity": {"planning_digest": "deadbeef"},
            "frozen_test_content_identity": "mismatch",
            "red_final_status": "PASS",
        }
        (green_dir / "GREEN.json").write_text(json.dumps(green) + "\n", encoding="utf-8")
        shutil.copytree(REPO_ROOT / "opsx_tools", root / "opsx_tools", dirs_exist_ok=True)

        native = _node_report(root, change)
        assert native["split_apply"] is True
        stale_rows = [
            s for s in native["slices"] if not (s.get("freshness") or {}).get("current", True)
        ]
        assert stale_rows, "native must classify STALE_GREEN"

        try:
            split_apply_green_identity(change, root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "SPLIT_APPLY_STALE"
        else:
            raise AssertionError("bounded must fail-closed on STALE_GREEN")


def main() -> int:
    test_stale_green_blocked_in_bounded_when_upstream_missing()
    print("split apply freshness parity tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
