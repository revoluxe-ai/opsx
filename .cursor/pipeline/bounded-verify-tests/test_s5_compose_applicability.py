# -*- coding: utf-8 -*-
"""Conditional obligation parity: compose/freeze check-set identity."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
sys.path.insert(0, str(BV_ROOT))


def _seed(root: Path, change: str) -> None:
    sys.path.insert(0, str(BV_ROOT.parent / "bounded-verify-tests"))
    from test_impl_seed import seed_hermetic_impl_ok

    for rel in (
        ".cursor/static-check/registry.yaml",
        "openspec/policies/VERIFY_POLICY.md",
        ".cursor/pipeline/bounded-verify/verification_obligations.py",
        "opsx/project/capabilities.yaml",
    ):
        src = REPO_ROOT / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    cdir = root / "openspec/changes" / change
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")
    for rel in ("src/stroy", "tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    seed_hermetic_impl_ok(change, root=root)


def test_scenario_not_applicable_excluded_from_conditional_ids() -> None:
    from bounded_verify_s5_obligation_applicability import applicable_conditional_check_ids

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed(root, "cond-na")
        ids = applicable_conditional_check_ids("cond-na", root=root)
        assert "scenario-test-coverage" not in ids
        assert "performance-review" not in ids


def test_scenario_mandatory_only_when_plan_present() -> None:
    from bounded_verify_s5_obligation_applicability import applicable_conditional_check_ids
    from bounded_verify_s5_hermetic_runtime import hermetic_s5_workspace

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "cond-scen"
        _seed(root, change)
        scen = root / "openspec/changes" / change / "scenario-coverage"
        scen.mkdir(parents=True)
        (scen / "plan.md").write_text("### 1. `Scenario: alpha`\n", encoding="utf-8")
        with hermetic_s5_workspace(root):
            ids = applicable_conditional_check_ids(change, root=root)
        assert "scenario-test-coverage" in ids


def test_mandatory_adapter_ids_exclude_conditional_only() -> None:
    from bounded_verify_s5_adapters import MANDATORY_S5_OBLIGATION_IDS

    assert "scenario-test-coverage" not in MANDATORY_S5_OBLIGATION_IDS
    assert "performance-review" not in MANDATORY_S5_OBLIGATION_IDS
    assert "external-call-coverage" in MANDATORY_S5_OBLIGATION_IDS


def main() -> int:
    test_scenario_not_applicable_excluded_from_conditional_ids()
    test_scenario_mandatory_only_when_plan_present()
    test_mandatory_adapter_ids_exclude_conditional_only()
    print("s5 compose applicability OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
