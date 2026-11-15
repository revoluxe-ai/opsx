# -*- coding: utf-8 -*-
"""Executable authority negative tests (not markdown lint)."""

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

from bounded_verify_plan import (  # noqa: E402
    FREEZE_BLOCKED_GAPS,
    PLAN_FROZEN_MUTATION_REFUSED,
    BoundedVerifyPlanError,
    draft_plan,
    freeze_plan,
)
from verification_plan_compose import compose_verification_plan  # noqa: E402


def _seed(root: Path) -> str:
    shutil.copytree(REPO_ROOT / "opsx/project", root / "opsx/project", dirs_exist_ok=True)
    fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
    change_id = str(fixture["change_id"])
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
    for rel in ("src/stroy", "tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    from test_impl_seed import seed_hermetic_impl_ok

    seed_hermetic_impl_ok(change_id, root=root)
    return change_id


def test_freeze_without_draft_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _seed(root)
        try:
            freeze_plan(change_id, root=root, registry_path=FIXTURES / "registry_snapshot.yaml")
        except BoundedVerifyPlanError as exc:
            assert exc.code == FREEZE_BLOCKED_GAPS
        else:
            raise AssertionError("freeze without draft must fail")


def test_draft_over_frozen_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _seed(root)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "pyright-strict", "order": 1}]
        draft_plan(change_id, checks, root=root, registry_path=registry)
        freeze_plan(change_id, root=root, registry_path=registry)
        try:
            draft_plan(change_id, checks, root=root, registry_path=registry)
        except BoundedVerifyPlanError as exc:
            assert exc.code == PLAN_FROZEN_MUTATION_REFUSED
        else:
            raise AssertionError("draft over frozen must fail")


def test_full_required_compose_fail_closed_not_static_fast() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _seed(root)
        try:
            compose_verification_plan(change_id, "full_required", root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "PLAN_COVERAGE_INCOMPLETE"
        else:
            raise AssertionError("full_required must not silently pass")
        fast = compose_verification_plan(change_id, "static_fast", root=root)
        assert "pyright-strict-coverage" not in fast.check_ids


def main() -> int:
    test_freeze_without_draft_refused()
    test_draft_over_frozen_refused()
    test_full_required_compose_fail_closed_not_static_fast()
    print("authority executable tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
