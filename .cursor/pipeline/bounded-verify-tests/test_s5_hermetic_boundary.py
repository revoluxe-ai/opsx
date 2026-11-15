# -*- coding: utf-8 -*-
"""P0: S5-A hermetic root isolation and fixture writer boundary."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_s5_adapters import adapter_implemented, run_s5_adapter  # noqa: E402
from bounded_verify_s5_evidence import validate_evidence_receipt_readonly  # noqa: E402
from bounded_verify_s5_hermetic_runtime import (  # noqa: E402
    HERMETIC_BOUNDARY_VIOLATION,
    REPO_ROOT_FIXTURE_REFUSED,
    HermeticBoundaryError,
    authorize_s5_hermetic_sandbox,
    clear_hermetic_s5_workspaces,
    hermetic_s5_workspace,
    register_hermetic_s5_workspace,
)
from bounded_verify_s5_receipt_fixture import (  # noqa: E402
    S5FixtureReceiptError,
    publish_fixture_receipt,
)
from bounded_verify_s5_fixture_semantics import FIXTURE_STATUS_PASS  # noqa: E402


def test_env_flag_does_not_enable_repo_root() -> None:
    os.environ["BOUNDED_VERIFY_S5_HERMETIC_ADAPTERS"] = "1"
    try:
        assert not adapter_implemented("external-call-coverage", root=REPO_ROOT)
        status, _ = run_s5_adapter("external-call-coverage", "x", root=REPO_ROOT)
        assert status == "EXECUTION_ERROR"
    finally:
        os.environ.pop("BOUNDED_VERIFY_S5_HERMETIC_ADAPTERS", None)
        clear_hermetic_s5_workspaces()


def test_repo_root_receipt_publication_refused() -> None:
    try:
        publish_fixture_receipt(
            "change",
            "external-call-coverage",
            execution_id="e1",
            plan_id="p",
            plan_fingerprint="a" * 64,
            input_digest="b" * 64,
            policy_identity={"change_id": "change"},
            evidence_contract="X_FIXTURE_TEST",
            status=FIXTURE_STATUS_PASS,
            check_evidence={"fixture": True},
            root=REPO_ROOT,
        )
    except (S5FixtureReceiptError, HermeticBoundaryError):
        pass
    else:
        raise AssertionError("REPO_ROOT publish must be refused")
    assert not list(
        (REPO_ROOT / "openspec/changes/change/bounded-verify/fixture-receipts").glob(
            "**/*"
        )
    )


def test_isolated_workspace_runs_with_registration() -> None:
    from test_s5_a_adapters import _seed_s5_root

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _seed_s5_root(root, "iso")
        with hermetic_s5_workspace(root):
            assert adapter_implemented("external-call-coverage", root=root)
        assert not adapter_implemented("external-call-coverage", root=root)


def test_symlink_escape_to_repo_root_blocked() -> None:
    with tempfile.TemporaryDirectory() as sandbox:
        authorize_s5_hermetic_sandbox(Path(sandbox))
        link = Path(sandbox) / "escape"
        link.symlink_to(REPO_ROOT, target_is_directory=True)
        try:
            register_hermetic_s5_workspace(link)
        except HermeticBoundaryError as exc:
            assert exc.code in ("REPO_ROOT_FIXTURE_REFUSED", HERMETIC_BOUNDARY_VIOLATION)
        else:
            raise AssertionError("symlink to REPO_ROOT must not register")
        clear_hermetic_s5_workspaces()


def test_repo_descendant_registration_refused() -> None:
    descendant = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify-tests" / "_s5_fixture_probe"
    descendant.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory() as sandbox:
            authorize_s5_hermetic_sandbox(Path(sandbox))
            try:
                register_hermetic_s5_workspace(descendant)
            except HermeticBoundaryError as exc:
                assert exc.code == REPO_ROOT_FIXTURE_REFUSED
            else:
                raise AssertionError("repo descendant must not register")
    finally:
        clear_hermetic_s5_workspaces()
        if descendant.is_dir():
            descendant.rmdir()


def test_unauthorized_tmp_without_sandbox_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            register_hermetic_s5_workspace(root)
        except HermeticBoundaryError as exc:
            assert exc.code in ("SANDBOX_NOT_AUTHORIZED", HERMETIC_BOUNDARY_VIOLATION)
        else:
            raise AssertionError("unauthorized TemporaryDirectory must not register")
        clear_hermetic_s5_workspaces()


def test_forged_fingerprint_without_frozen_plan_refused_zero_writes() -> None:
    from test_s5_a_adapters import _seed_s5_root

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "no-freeze"
        _seed_s5_root(root, change)
        with hermetic_s5_workspace(root):
            try:
                publish_fixture_receipt(
                    change,
                    "external-call-coverage",
                    execution_id="exec-1",
                    plan_id="forged-plan",
                    plan_fingerprint="f" * 64,
                    input_digest="a" * 64,
                    policy_identity={"change_id": change},
                    evidence_contract="X_FIXTURE_TEST",
                    status=FIXTURE_STATUS_PASS,
                    check_evidence={"fixture": True},
                    root=root,
                )
            except S5FixtureReceiptError as exc:
                assert exc.code == "FROZEN_PLAN_BINDING_REQUIRED"
            else:
                raise AssertionError("forged fingerprint without FROZEN plan must be refused")
        receipts = list(
            (root / "openspec/changes" / change / "bounded-verify/fixture-receipts").glob(
                "**/*"
            )
        )
        assert not receipts


def test_wrong_plan_binding_vs_frozen_refused() -> None:
    from test_s5_a_adapters import _freeze_minimal_plan, _seed_s5_root

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "frozen-mismatch"
        _seed_s5_root(root, change)
        _freeze_minimal_plan(root, change)
        with hermetic_s5_workspace(root):
            try:
                publish_fixture_receipt(
                    change,
                    "external-call-coverage",
                    execution_id="exec-2",
                    plan_id="wrong-plan-id",
                    plan_fingerprint="0" * 64,
                    input_digest="a" * 64,
                    policy_identity={"change_id": change},
                    evidence_contract="X_FIXTURE_TEST",
                    status=FIXTURE_STATUS_PASS,
                    check_evidence={"fixture": True},
                    root=root,
                )
            except S5FixtureReceiptError as exc:
                assert exc.code == "FROZEN_PLAN_BINDING_REFUSED"
            else:
                raise AssertionError("wrong plan binding must be refused")
        receipts = list(
            (root / "openspec/changes" / change / "bounded-verify/fixture-receipts").glob(
                "**/*"
            )
        )
        assert not receipts


def test_receipt_path_traversal_ids_refused_zero_writes() -> None:
    from test_s5_a_adapters import _freeze_minimal_plan, _seed_s5_root

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "path-safe"
        _seed_s5_root(root, change)
        _freeze_minimal_plan(root, change)
        with hermetic_s5_workspace(root):
            for bad_change, bad_obligation, bad_exec in (
                ("../escape", "external-call-coverage", "e1"),
                ("path-safe", "..", "e1"),
                ("path-safe", "external-call-coverage", "../evil"),
            ):
                try:
                    publish_fixture_receipt(
                        bad_change,
                        bad_obligation,
                        execution_id=bad_exec,
                        plan_id="",
                        plan_fingerprint="",
                        input_digest="a" * 64,
                        policy_identity={"change_id": change},
                        evidence_contract="X_FIXTURE_TEST",
                        status=FIXTURE_STATUS_PASS,
                        check_evidence={"fixture": True},
                        root=root,
                    )
                except (S5FixtureReceiptError, HermeticBoundaryError) as exc:
                    code = getattr(exc, "code", "")
                    assert code in (
                        "PATH_COMPONENT_INVALID",
                        HERMETIC_BOUNDARY_VIOLATION,
                        "FROZEN_PLAN_BINDING_REFUSED",
                    )
                else:
                    raise AssertionError("path traversal ids must be refused")
        receipts = list(
            (root / "openspec/changes" / change / "bounded-verify/fixture-receipts").glob(
                "**/*"
            )
        )
        assert not receipts


def test_production_evidence_gate_rejects_fixture_receipt() -> None:
    from test_s5_a_adapters import _freeze_minimal_plan, _seed_s5_root

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "fixture-gate"
        _seed_s5_root(root, change)
        _freeze_minimal_plan(root, change)
        with hermetic_s5_workspace(root):
            from bounded_verify_plan_profile import policy_revision_bindings

            bindings = policy_revision_bindings(root=root)
            policy_identity = {
                "change_id": change,
                "verification_obligations_revision": bindings.get(
                    ".cursor/pipeline/bounded-verify/verification_obligations.py",
                    "",
                ),
                "verify_policy_revision": bindings.get(
                    "openspec/policies/VERIFY_POLICY.md", ""
                ),
            }
            from bounded_verify_s5_common import input_digest_for_change, require_frozen_plan_binding

            plan_id, plan_fp = require_frozen_plan_binding(change, root=root)
            rec = publish_fixture_receipt(
                change,
                "external-call-coverage",
                execution_id="exec-gate",
                plan_id=plan_id,
                plan_fingerprint=plan_fp,
                input_digest=input_digest_for_change(change, root=root),
                policy_identity=policy_identity,
                evidence_contract="EXTERNAL_CALL_COVERAGE_FIXTURE_TEST",
                status=FIXTURE_STATUS_PASS,
                check_evidence={"fixture": True},
                root=root,
            )
            out = validate_evidence_receipt_readonly(
                "external-call-coverage",
                rec.carrier_path,
                root=REPO_ROOT,
            )
            assert out["gate_acceptance"] != "ACCEPTED"


def test_forged_enabled_marker_without_registration_blocked() -> None:
    from bounded_verify_s5_adapters import adapter_implemented

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        marker = root / ".cursor/pipeline/bounded-verify-tests/fixtures/s5"
        marker.mkdir(parents=True)
        (marker / "hermetic-adapters.enabled").write_text("1\n", encoding="utf-8")
        os.environ["BOUNDED_VERIFY_S5_HERMETIC_ADAPTERS"] = "1"
        try:
            assert not adapter_implemented("external-call-coverage", root=root)
        finally:
            os.environ.pop("BOUNDED_VERIFY_S5_HERMETIC_ADAPTERS", None)
            clear_hermetic_s5_workspaces()


def test_registration_does_not_leak_between_cases() -> None:
    from bounded_verify_s5_hermetic_runtime import s5_hermetic_adapters_enabled

    clear_hermetic_s5_workspaces()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        authorize_s5_hermetic_sandbox(root)
        register_hermetic_s5_workspace(root)
        assert s5_hermetic_adapters_enabled(root=root)
        clear_hermetic_s5_workspaces()
        assert not s5_hermetic_adapters_enabled(root=root)


def main() -> int:
    test_env_flag_does_not_enable_repo_root()
    test_repo_root_receipt_publication_refused()
    test_isolated_workspace_runs_with_registration()
    test_symlink_escape_to_repo_root_blocked()
    test_repo_descendant_registration_refused()
    test_unauthorized_tmp_without_sandbox_refused()
    test_forged_fingerprint_without_frozen_plan_refused_zero_writes()
    test_wrong_plan_binding_vs_frozen_refused()
    test_receipt_path_traversal_ids_refused_zero_writes()
    test_production_evidence_gate_rejects_fixture_receipt()
    test_forged_enabled_marker_without_registration_blocked()
    test_registration_does_not_leak_between_cases()
    print("s5 hermetic boundary OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
