# -*- coding: utf-8 -*-
"""P0: conditional scenario applicability + pyright WORKTREE coverage."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import BoundedVerifyPlanError, draft_plan, freeze_plan  # noqa: E402
from bounded_verify_s5_obligation_applicability import (  # noqa: E402
    ApplicabilityDisposition,
    resolve_scenario_test_coverage_applicability,
)
from bounded_verify_s5_pyright_coverage import audit_pyright_strict_coverage  # noqa: E402
from test_impl_seed import seed_hermetic_impl_ok  # noqa: E402
from verification_plan_compose import compose_verification_plan  # noqa: E402


def _seed_change(root: Path, change_id: str = "p0-change") -> None:
    cap = root / "opsx" / "project" / "capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx" / "project" / "capabilities.yaml", cap)
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    seed_hermetic_impl_ok(change_id, root=root)


def test_scenario_applicable_blocks_full_required_freeze() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "scenario-change"
        _seed_change(root, change_id)
        plan_dir = root / "openspec/changes" / change_id / "scenario-coverage"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.md").write_text(
            "### 1. `Scenario: login flow`\n| required_test_artifacts | tests/unit/test_login.py |\n",
            encoding="utf-8",
        )
        row = resolve_scenario_test_coverage_applicability(change_id, root=root)
        assert row.disposition == ApplicabilityDisposition.MANDATORY_APPLICABLE
        try:
            compose_verification_plan(change_id, "full_required", root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "PLAN_COVERAGE_INCOMPLETE"
        else:
            raise AssertionError("full_required must block when scenario applicable")


def test_pyright_dirty_tracked_file_fails_coverage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root, "py-change")
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        (root / "pyrightconfig_strict.json").write_text(
            json.dumps({"include": ["src/stroy/"], "exclude": []}),
            encoding="utf-8",
        )
        (root / "src/stroy").mkdir(parents=True)
        tracked = root / "src/stroy/tracked.py"
        tracked.write_text("x=1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)
        tracked.write_text("x=2\n", encoding="utf-8")
        import os

        os.environ["BOUNDED_VERIFY_COVERAGE_BASE_REF"] = "HEAD"
        report = audit_pyright_strict_coverage(root=root)
        assert report["STATUS"] == "PASS"
        assert "src/stroy/tracked.py" in report["audited_paths"]


def test_single_checkbox_insufficient_for_freeze() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "impl-gap"
        _seed_change(root, change_id)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "pyright-strict", "order": 1}]
        draft_plan(change_id, checks, root=root, registry_path=registry)
        path = root / "openspec/changes" / change_id / "bounded-verify/impl-completion-projection.json"
        path.write_text(
            json.dumps(
                {
                    "split_apply": True,
                    "marker": "IMPL_GAPS",
                    "blocking": [{"class": "GAP_PRODUCT_IMPLEMENTATION"}],
                    "test_authority_mark": "bounded-verify-hermetic-test-authority-v1-do-not-use-in-production",
                    "policy": "HERMETIC_TEST_PROJECTION",
                }
            ),
            encoding="utf-8",
        )
        try:
            freeze_plan(change_id, root=root, registry_path=registry)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "IMPL_NOT_READY"
        else:
            raise AssertionError("freeze must require IMPL_OK projection")


def main() -> int:
    test_scenario_applicable_blocks_full_required_freeze()
    test_pyright_dirty_tracked_file_fails_coverage()
    test_single_checkbox_insufficient_for_freeze()
    print("p0 conditional and pyright tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
