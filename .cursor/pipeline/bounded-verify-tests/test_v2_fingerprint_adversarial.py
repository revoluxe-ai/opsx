# -*- coding: utf-8 -*-
"""P0-C: v2 fingerprint and policy integrity adversarial cases."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import (  # noqa: E402
    PLAN_INVALID,
    BoundedVerifyPlanError,
    freeze_plan,
    plan_carrier_path,
    read_plan,
)
from bounded_verify_plan_profile import FULL_REQUIRED_VERIFICATION_COMPLETE  # noqa: E402
from test_impl_seed import seed_hermetic_impl_ok  # noqa: E402
from verification_plan_compose import compose_verification_plan  # noqa: E402


def _seed(root: Path, change_id: str = "fp-adv") -> None:
    cap = root / "opsx/project/capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx/project/capabilities.yaml", cap)
    cdir = root / "openspec/changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    for rel in ("src/stroy", "tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    seed_hermetic_impl_ok(change_id, root=root)


def test_tampered_fingerprint_rejected_on_read() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "tamper-fp"
        _seed(root, change_id)
        compose_verification_plan(change_id, "static_fast", root=root)
        from bounded_verify_plan import DEFAULT_REGISTRY_PATH

        freeze_plan(change_id, root=root, registry_path=DEFAULT_REGISTRY_PATH)
        path = plan_carrier_path(change_id, root=root)
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["plan_fingerprint"] = "0" * 64
        path.write_text(json.dumps(doc), encoding="utf-8")
        try:
            read_plan(change_id, root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == PLAN_INVALID
        else:
            raise AssertionError("tampered fingerprint must be rejected")


def test_forged_full_required_label_without_policy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "forge-full"
        _seed(root, change_id)
        compose_verification_plan(change_id, "static_fast", root=root)
        from bounded_verify_plan import DEFAULT_REGISTRY_PATH

        freeze_plan(change_id, root=root, registry_path=DEFAULT_REGISTRY_PATH)
        path = plan_carrier_path(change_id, root=root)
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["verification_profile"] = "full_required"
        path.write_text(json.dumps(doc), encoding="utf-8")
        try:
            read_plan(change_id, root=root)
        except BoundedVerifyPlanError:
            pass
        else:
            raise AssertionError("forged profile must break fingerprint validation")


def main() -> int:
    test_tampered_fingerprint_rejected_on_read()
    test_forged_full_required_label_without_policy()
    print("v2 fingerprint adversarial tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
