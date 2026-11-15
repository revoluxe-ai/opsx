# -*- coding: utf-8 -*-
"""S5-A: hermetic adapter implementation + fixture receipt acceptance."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
FIXTURES = BV_ROOT.parent / "bounded-verify-tests/fixtures"
sys.path.insert(0, str(BV_ROOT))


def _seed_s5_root(root: Path, change_id: str) -> None:
    sys.path.insert(0, str(FIXTURES.parent))
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
    s5 = FIXTURES / "s5"
    s5_dst = root / ".cursor/pipeline/bounded-verify-tests/fixtures/s5"
    s5_dst.mkdir(parents=True, exist_ok=True)
    for name in (
        "hermetic-adapters.enabled",
        "external-call-surfaces.json",
        "antipattern-rules.json",
    ):
        shutil.copy2(s5 / name, s5_dst / name)
    (root / "src/stroy").mkdir(parents=True, exist_ok=True)
    (root / "src/stroy/hermetic_client.py").write_text(
        "import httpx\n\ndef call():\n    return httpx.Client()\n",
        encoding="utf-8",
    )
    (root / "tests/unit").mkdir(parents=True, exist_ok=True)
    (root / "tests/unit/test_hermetic_client.py").write_text(
        "def test_client():\n    assert True\n",
        encoding="utf-8",
    )
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    seed_hermetic_impl_ok(change_id, root=root)


def _freeze_minimal_plan(root: Path, change_id: str) -> None:
    from bounded_verify_plan import draft_plan, freeze_plan

    reg = FIXTURES / "registry_snapshot.yaml"
    draft_plan(
        change_id,
        [
            {"check_id": "pyright-strict", "order": 1},
            {"check_id": "mypy", "order": 2},
        ],
        root=root,
        registry_path=reg,
    )
    freeze_plan(change_id, root=root, registry_path=reg)


def test_s5_adapters_ready_in_hermetic() -> None:
    from bounded_verify_s5_adapters import adapter_implemented
    from bounded_verify_s5_hermetic_runtime import hermetic_s5_workspace

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_s5_root(root, "s5-a")
        with hermetic_s5_workspace(root):
            for oid in (
                "external-call-coverage",
                "antipattern-gate",
                "static-check-synthesis",
                "scenario-test-coverage",
                "performance-review",
            ):
                assert adapter_implemented(oid, root=root), oid


def test_fixture_receipt_idempotent_and_validator() -> None:
    from bounded_verify_s5_adapters import run_s5_adapter
    from bounded_verify_s5_evidence import validate_fixture_evidence_receipt_readonly
    from bounded_verify_s5_fixture_semantics import FIXTURE_STATUS_PASS
    from bounded_verify_s5_hermetic_runtime import hermetic_s5_workspace
    from bounded_verify_s5_receipt_fixture import (
        S5FixtureReceiptError,
        publish_fixture_receipt,
    )
    from bounded_verify_s5_runners import adapter_runner_packet_with_identity

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "s5-receipt"
        _seed_s5_root(root, change)
        _freeze_minimal_plan(root, change)
        with hermetic_s5_workspace(root):
            status, packet = run_s5_adapter("external-call-coverage", change, root=root)
            assert status == "INCOMPLETE"
            assert packet["STATUS"] == FIXTURE_STATUS_PASS
            enriched = adapter_runner_packet_with_identity(
                change, "external-call-coverage", root, packet
            )
            exec_id = "exec-s5-a-1"
            rec = publish_fixture_receipt(
                change,
                "external-call-coverage",
                execution_id=exec_id,
                plan_id=str(enriched["plan_id"]),
                plan_fingerprint=str(enriched["plan_fingerprint"]),
                input_digest=str(enriched["input_digest"]),
                policy_identity=dict(enriched["policy_identity"]),
                evidence_contract=str(packet["evidence_contract"]),
                status=FIXTURE_STATUS_PASS,
                check_evidence=dict(packet["check_evidence"]),
                root=root,
            )
            publish_fixture_receipt(
                change,
                "external-call-coverage",
                execution_id=exec_id,
                plan_id=str(enriched["plan_id"]),
                plan_fingerprint=str(enriched["plan_fingerprint"]),
                input_digest=str(enriched["input_digest"]),
                policy_identity=dict(enriched["policy_identity"]),
                evidence_contract=str(packet["evidence_contract"]),
                status=FIXTURE_STATUS_PASS,
                check_evidence=dict(packet["check_evidence"]),
                root=root,
            )
            out = validate_fixture_evidence_receipt_readonly(
                "external-call-coverage",
                rec.carrier_path,
                bound_policy_identity=dict(enriched["policy_identity"]),
                bound_input_digest=str(enriched["input_digest"]),
                bound_execution_id=exec_id,
            )
            assert out["gate_acceptance"] == "ACCEPTED"
            try:
                publish_fixture_receipt(
                    change,
                    "external-call-coverage",
                    execution_id=exec_id,
                    plan_id="other",
                    plan_fingerprint="1" * 64,
                    input_digest="2" * 64,
                    policy_identity={},
                    evidence_contract=str(packet["evidence_contract"]),
                    status=FIXTURE_STATUS_PASS,
                    check_evidence={"forged": True},
                    root=root,
                )
            except S5FixtureReceiptError as exc:
                assert exc.code == "FROZEN_PLAN_BINDING_REFUSED"
            else:
                raise AssertionError("immutable conflict expected")


def test_external_call_fail_without_test() -> None:
    from bounded_verify_s5_adapters import run_s5_adapter
    from bounded_verify_s5_fixture_semantics import FIXTURE_STATUS_FAIL
    from bounded_verify_s5_hermetic_runtime import hermetic_s5_workspace

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "s5-fail"
        _seed_s5_root(root, change)
        with hermetic_s5_workspace(root):
            (root / "tests/unit/test_hermetic_client.py").unlink()
            status, packet = run_s5_adapter("external-call-coverage", change, root=root)
            assert status == "FAIL"
            assert packet["STATUS"] == FIXTURE_STATUS_FAIL


def test_production_root_stays_unsupported() -> None:
    from bounded_verify_s5_adapters import adapter_implemented

    assert not adapter_implemented("external-call-coverage", root=REPO_ROOT)


def main() -> int:
    test_s5_adapters_ready_in_hermetic()
    test_fixture_receipt_idempotent_and_validator()
    test_external_call_fail_without_test()
    test_production_root_stays_unsupported()
    print("S5-A adapter tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
