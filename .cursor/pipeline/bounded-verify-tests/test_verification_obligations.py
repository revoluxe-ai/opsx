# -*- coding: utf-8 -*-
"""Negative tests: full_required fail-closed coverage (no false-green)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import BoundedVerifyPlanError  # noqa: E402
from bounded_verify_s5_adapters import adapter_implemented  # noqa: E402
from verification_obligations import (  # noqa: E402
    frontier_adapter_obligations,
    obligations_for_profile,
    unsupported_mandatory_obligations,
)
from verification_plan_compose import compose_verification_plan  # noqa: E402


def _seed_change(root: Path, change_id: str) -> None:
    cap = root / "opsx" / "project" / "capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    cap.write_text(
        (REPO_ROOT / "opsx" / "project" / "capabilities.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    cdir = root / "openspec" / "changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    from test_impl_seed import seed_hermetic_impl_ok

    seed_hermetic_impl_ok(change_id, root=root)


def test_full_required_lists_adapter_obligations() -> None:
    unsupported = unsupported_mandatory_obligations("full_required")
    ids = {o.obligation_id for o in unsupported}
    assert "external-call-coverage" in ids
    assert not adapter_implemented("external-call-coverage")


def test_full_required_obligation_count_includes_adapters() -> None:
    total = obligations_for_profile("full_required")
    assert len(total) > 6
    assert len(frontier_adapter_obligations("full_required")) >= 4


def test_full_required_compose_raises_plan_coverage_incomplete() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root, "cov-change")
        try:
            compose_verification_plan("cov-change", "full_required", root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "PLAN_COVERAGE_INCOMPLETE"
        else:
            raise AssertionError("expected PLAN_COVERAGE_INCOMPLETE")


def test_allow_incomplete_coverage_forbidden_for_full_required() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root, "cov-change")
        try:
            compose_verification_plan(
                "cov-change",
                "full_required",
                root=root,
                allow_incomplete_coverage=True,
            )
        except BoundedVerifyPlanError as exc:
            assert exc.code == "PLAN_COVERAGE_INCOMPLETE"
            assert "allow_incomplete_coverage" in exc.message
        else:
            raise AssertionError("expected fail-closed on allow_incomplete_coverage")


def main() -> int:
    test_full_required_lists_adapter_obligations()
    test_full_required_obligation_count_includes_adapters()
    test_full_required_compose_raises_plan_coverage_incomplete()
    test_allow_incomplete_coverage_forbidden_for_full_required()
    print("verification_obligations tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
