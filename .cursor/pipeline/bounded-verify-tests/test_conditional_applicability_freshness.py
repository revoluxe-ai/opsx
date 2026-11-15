# -*- coding: utf-8 -*-
"""P0-B: conditional applicability bound to snapshot + schedule drift."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_ledger import BoundedVerifyLedgerError, schedule_write  # noqa: E402
from bounded_verify_plan import freeze_plan  # noqa: E402
from bounded_verify_s5_obligation_applicability import (  # noqa: E402
    assert_conditional_applicability_current,
)
from test_impl_seed import seed_hermetic_impl_ok  # noqa: E402
from verification_plan_compose import compose_verification_plan  # noqa: E402


def _seed(root: Path, change_id: str) -> None:
    cap = root / "opsx/project/capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx/project/capabilities.yaml", cap)
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    for rel in ("src/stroy", "tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    seed_hermetic_impl_ok(change_id, root=root)


def test_scenario_plan_after_freeze_blocks_schedule() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "cond-freeze"
        _seed(root, change_id)
        compose_verification_plan(change_id, "static_fast", root=root)
        freeze_plan(change_id, root=root)
        plan_dir = root / "openspec/changes" / change_id / "scenario-coverage"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.md").write_text(
            "### 1. `Scenario: x`\n",
            encoding="utf-8",
        )
        from bounded_verify_plan import read_plan

        plan = read_plan(change_id, root=root)
        try:
            assert_conditional_applicability_current(plan, change_id, root=root)
        except Exception:
            pass
        else:
            raise AssertionError("expected applicability drift")
        try:
            schedule_write(change_id, root=root)
        except BoundedVerifyLedgerError as exc:
            assert "DRIFT" in exc.code or exc.code == "BLOCKED_SNAPSHOT_DRIFT"
        else:
            raise AssertionError("schedule must block on conditional drift")


def main() -> int:
    test_scenario_plan_after_freeze_blocks_schedule()
    print("conditional applicability freshness OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
