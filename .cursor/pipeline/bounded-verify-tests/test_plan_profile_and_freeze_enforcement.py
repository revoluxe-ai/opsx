# -*- coding: utf-8 -*-
"""P0-A/B: profile identity, freeze mandatory coverage, static non-full proof."""

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
    BoundedVerifyPlanError,
    draft_plan,
    freeze_plan,
    read_plan,
)
from bounded_verify_plan_profile import (  # noqa: E402
    BOUNDED_STATIC_VERIFICATION_COMPLETE,
    FULL_REQUIRED_VERIFICATION_COMPLETE,
    LEGACY_BOUNDED_VERIFICATION_COMPLETE,
    ledger_completion_attestation,
)
from bounded_verify_snapshot import PLAN_SCHEMA_V2  # noqa: E402
from test_impl_seed import seed_hermetic_impl_ok  # noqa: E402
from verification_plan_compose import compose_verification_plan  # noqa: E402


def _seed(root: Path, change_id: str = "prof-change") -> None:
    cap = root / "opsx/project/capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx/project/capabilities.yaml", cap)
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    seed_hermetic_impl_ok(change_id, root=root)


def test_compose_static_fast_publishes_v2_profile_and_snapshot() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "v2-static"
        _seed(root, change_id)
        compose_verification_plan(change_id, "static_fast", root=root)
        doc = read_plan(change_id, root=root)
        assert doc["schema_version"] == PLAN_SCHEMA_V2
        assert doc["verification_profile"] == "static_fast"
        assert "role_digests" in doc["implementation_snapshot"]
        assert "verification_policy" in doc
        frozen = freeze_plan(change_id, root=root, registry_path=FIXTURES / "registry_snapshot.yaml")
        assert frozen.noop is False or frozen.noop is True
        doc2 = read_plan(change_id, root=root)
        assert doc2["plan_status"] == "FROZEN"
        assert ledger_completion_attestation(doc2) == BOUNDED_STATIC_VERIFICATION_COMPLETE
        assert ledger_completion_attestation(doc2) != FULL_REQUIRED_VERIFICATION_COMPLETE


def test_manual_static_draft_cannot_pose_as_full_required() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "adv-static"
        _seed(root, change_id)
        checks = [
            {"check_id": "pyright-strict", "order": 1},
            {"check_id": "mypy", "order": 2},
        ]
        try:
            draft_plan(
                change_id,
                checks,
                root=root,
                registry_path=FIXTURES / "registry_snapshot.yaml",
                verification_profile="full_required",
            )
        except BoundedVerifyPlanError as exc:
            assert exc.code == "PLAN_COVERAGE_INCOMPLETE"
        else:
            raise AssertionError("static-only checks cannot draft as full_required")


def test_legacy_v1_freeze_attestation_not_full_required() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "legacy-v1"
        _seed(root, change_id)
        fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
        checks = list(fixture["required_checks"])
        draft_plan(
            change_id,
            checks,
            root=root,
            registry_path=FIXTURES / "registry_snapshot.yaml",
        )
        doc = read_plan(change_id, root=root)
        assert doc["schema_version"] == "bounded-verify-plan-v1"
        assert "verification_profile" not in doc
        freeze_plan(change_id, root=root, registry_path=FIXTURES / "registry_snapshot.yaml")
        frozen = read_plan(change_id, root=root)
        assert ledger_completion_attestation(frozen) == LEGACY_BOUNDED_VERIFICATION_COMPLETE
        assert ledger_completion_attestation(frozen) != FULL_REQUIRED_VERIFICATION_COMPLETE


def test_env_only_hermetic_impl_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "no-token"
        import os

        os.environ["BOUNDED_VERIFY_HERMETIC_IMPL"] = "1"
        cdir = root / "openspec/changes" / change_id
        cdir.mkdir(parents=True)
        (cdir / "tasks.md").write_text("- [x] x\n", encoding="utf-8")
        proj = cdir / "bounded-verify/impl-completion-projection.json"
        proj.parent.mkdir(parents=True)
        proj.write_text(
            json.dumps({"split_apply": True, "marker": "IMPL_OK", "blocking": []}),
            encoding="utf-8",
        )
        from bounded_verify_impl_completion import assert_implementation_ready_for_freeze

        try:
            assert_implementation_ready_for_freeze(change_id, root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code in ("IMPL_COMPLETION_BLOCKED", "IMPL_NOT_READY", "IMPL_COMPLETION_UNKNOWN")
        else:
            raise AssertionError("env-only hermetic must not authorize freeze")
