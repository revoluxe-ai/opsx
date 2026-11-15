# -*- coding: utf-8 -*-
"""P0: S5 adapters must not synthetic-PASS; full_required fail-closed."""

from __future__ import annotations

import json
import os
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
from bounded_verify_s5_adapters import (  # noqa: E402
    AdapterCapability,
    adapter_capability_status,
    adapter_implemented,
    run_s5_adapter,
)
from bounded_verify_s5_pyright_coverage import audit_pyright_strict_coverage  # noqa: E402
from verification_applicability import ApplicabilityResult, applicability_for_obligation  # noqa: E402
from verification_obligations import VerificationObligation, ObligationKind  # noqa: E402
from verification_plan_compose import compose_verification_plan  # noqa: E402


def _seed_change(root: Path, change_id: str = "s5-adv") -> None:
    cap = root / "opsx" / "project" / "capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx" / "project" / "capabilities.yaml", cap)
    cdir = root / "openspec" / "changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    (root / "src/stroy").mkdir(parents=True, exist_ok=True)
    for rel in ("tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)


def test_only_pyright_installed_not_coverage_pass() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root)
        assert adapter_capability_status("pyright-strict-coverage", root=root) in (
            AdapterCapability.UNSUPPORTED,
        )
        status, packet = run_s5_adapter("pyright-strict-coverage", "s5-adv", root=root)
        assert status == "EXECUTION_ERROR"
        assert packet.get("code") == "ADAPTER_UNSUPPORTED"


def test_external_call_not_pass_without_native() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root)
        status, packet = run_s5_adapter("external-call-coverage", "s5-adv", root=root)
        assert status != "PASS"
        assert packet.get("code") == "ADAPTER_UNSUPPORTED"


def test_antipattern_policy_file_alone_not_pass() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root)
        policy = root / ".cursor/commands/opsx-antipattern-gate.md"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("# policy\n", encoding="utf-8")
        status, _packet = run_s5_adapter("antipattern-gate", "s5-adv", root=root)
        assert status != "PASS"


def test_registry_alone_not_static_synthesis_pass() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root)
        reg = root / ".cursor/static-check/registry.yaml"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text("version: 1\nchecks: {}\n", encoding="utf-8")
        status, _ = run_s5_adapter("static-check-synthesis", "s5-adv", root=root)
        assert status != "PASS"


def test_scenarios_without_mapped_tests_not_pass() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root)
        specs = root / "openspec/changes/s5-adv/specs"
        specs.mkdir(parents=True)
        (specs / "scenario.md").write_text("# scenario\n", encoding="utf-8")
        status, _ = run_s5_adapter("scenario-test-coverage", "s5-adv", root=root)
        assert status != "PASS"


def test_full_required_compose_blocked_without_adapters() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root)
        try:
            compose_verification_plan("s5-adv", "full_required", root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "PLAN_COVERAGE_INCOMPLETE"
        else:
            raise AssertionError("full_required must not compose without READY S5 adapters")


def test_draft_plan_bypass_blocked_for_s5_check_id() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "external-call-coverage", "order": 99}]
        try:
            draft_plan("s5-adv", checks, root=root, registry_path=registry)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "PLAN_COVERAGE_INCOMPLETE"
        else:
            raise AssertionError("direct draft must not bypass S5 mandatory policy")


def test_applicability_parity_unsupported_when_not_ready() -> None:
    obligation = VerificationObligation(
        obligation_id="external-call-coverage",
        kind=ObligationKind.MANDATORY,
        registry_check_id=None,
        evidence_contract="X",
        policy_source="y",
    )
    row = applicability_for_obligation(obligation)
    assert row.result == ApplicabilityResult.UNSUPPORTED_FAIL_CLOSED


def test_pyright_coverage_fail_on_missing_include() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
        cfg = {
            "include": ["src/stroy/existing_only/"],
            "exclude": [],
            "typeCheckingMode": "strict",
        }
        (root / "pyrightconfig_strict.json").write_text(json.dumps(cfg), encoding="utf-8")
        new_file = root / "src/stroy/notifications/new.py"
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text("x = 1\n", encoding="utf-8")
        os.environ["BOUNDED_VERIFY_COVERAGE_BASE_REF"] = "HEAD"
        report = audit_pyright_strict_coverage(root=root)
        assert report["STATUS"] == "FAIL"
        status, packet = run_s5_adapter("pyright-strict-coverage", "s5-adv", root=root)
        assert status == "FAIL"
        assert packet.get("gaps")


def test_pyright_coverage_pass_when_covered() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_change(root)
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
        (root / "pyrightconfig_strict.json").write_text(
            json.dumps({"include": ["src/stroy/"], "exclude": []}),
            encoding="utf-8",
        )
        path = root / "src/stroy/module.py"
        path.write_text("def f() -> int:\n    return 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "src/stroy/module.py"], cwd=root, check=True, capture_output=True)
        os.environ["BOUNDED_VERIFY_COVERAGE_BASE_REF"] = "HEAD"
        report = audit_pyright_strict_coverage(root=root)
        assert report["STATUS"] == "PASS"
        assert adapter_implemented("pyright-strict-coverage", root=root)
        status, _ = run_s5_adapter("pyright-strict-coverage", "s5-adv", root=root)
        assert status == "PASS"


def main() -> int:
    test_only_pyright_installed_not_coverage_pass()
    test_external_call_not_pass_without_native()
    test_antipattern_policy_file_alone_not_pass()
    test_registry_alone_not_static_synthesis_pass()
    test_scenarios_without_mapped_tests_not_pass()
    test_full_required_compose_blocked_without_adapters()
    test_draft_plan_bypass_blocked_for_s5_check_id()
    test_applicability_parity_unsupported_when_not_ready()
    test_pyright_coverage_fail_on_missing_include()
    test_pyright_coverage_pass_when_covered()
    print("s5 false-green adversarial tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
