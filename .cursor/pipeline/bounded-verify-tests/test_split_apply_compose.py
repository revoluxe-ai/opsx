# -*- coding: utf-8 -*-
"""Split-APPLY compose + freeze без tasks.md [x]."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

sys.path.insert(0, str(BV_ROOT))

from bounded_verify_compose import compose_required_checks  # noqa: E402
from bounded_verify_impl_completion import (  # noqa: E402
    IMPL_COMPLETION_BLOCKED,
    IMPL_COMPLETION_UNKNOWN,
    IMPL_NOT_READY,
    assert_implementation_ready_for_freeze,
    fetch_impl_completion_report,
    implementation_completion_digest,
    write_hermetic_impl_projection,
)
from bounded_verify_plan import (  # noqa: E402
    PLAN_FROZEN_MUTATION_REFUSED,
    BoundedVerifyPlanError,
    assert_plan_mutable,
    draft_plan,
    freeze_plan,
    read_plan,
)
from bounded_verify_snapshot import assert_snapshot_fresh  # noqa: E402
from test_impl_seed import seed_hermetic_impl_ok  # noqa: E402


def _assert_v1_snapshot_schema_only(snap: dict) -> None:
    """Plan carrier snapshot: только content_digest (completion — вне plan)."""
    assert set(snap.keys()) == {"content_digest"}
    digest = str(snap["content_digest"])
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_split_apply_frozen_read_contract() -> None:
    """FROZEN split-APPLY: compose + schema snapshot; без draft_plan мутации."""
    change_id = "invest-yalta-max-funnel"
    checks = compose_required_checks(change_id, root=REPO_ROOT)
    assert any(c["check_id"] == "opsx-split-apply-impl-ready" for c in checks)
    assert checks[0]["order"] == 1

    plan_before = read_plan(change_id, root=REPO_ROOT)
    assert plan_before["plan_status"] == "FROZEN"
    fp_before = plan_before["plan_fingerprint"]

    plan = read_plan(change_id, root=REPO_ROOT)
    assert plan["plan_fingerprint"] == fp_before
    snap = plan["implementation_snapshot"]
    _assert_v1_snapshot_schema_only(snap)
    assert snap.get("completion_policy") is None
    assert snap.get("split_apply_marker") is None

    try:
        assert_plan_mutable(change_id, root=REPO_ROOT)
    except BoundedVerifyPlanError as exc:
        assert exc.code == PLAN_FROZEN_MUTATION_REFUSED
    else:
        raise AssertionError("FROZEN plan must refuse mutation")


def test_split_apply_impl_ok_via_hermetic_completion_report() -> None:
    """IMPL_OK authority: hermetic completion report, не metadata в plan snapshot."""
    fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "split-apply-impl-ok"
        change_dir = root / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True)
        (change_dir / "tasks.md").write_text("- [ ] open only\n", encoding="utf-8")
        seed_hermetic_impl_ok(change_id, root=root)

        report = fetch_impl_completion_report(change_id, root=root)
        assert report.get("split_apply") is True
        assert report.get("marker") == "IMPL_OK"
        ready = assert_implementation_ready_for_freeze(change_id, root=root)
        assert ready.get("marker") == "IMPL_OK"

        reg = FIXTURES / "registry_snapshot.yaml"
        checks = list(fixture["required_checks"])
        draft_plan(change_id, checks, root=root, registry_path=reg)
        plan = read_plan(change_id, root=root)
        snap = plan["implementation_snapshot"]
        _assert_v1_snapshot_schema_only(snap)
        live_digest = implementation_completion_digest(change_id, root=root)
        assert snap["content_digest"] == live_digest

        frozen = freeze_plan(change_id, root=root, registry_path=reg)
        assert frozen.noop is False
        frozen_doc = read_plan(change_id, root=root)
        assert frozen_doc["plan_status"] == "FROZEN"
        _assert_v1_snapshot_schema_only(frozen_doc["implementation_snapshot"])


def test_legacy_tasks_freeze_blocked_without_completion_authority() -> None:
    """tasks.md [x] не заменяет split-APPLY completion; snapshot без legacy policy."""
    fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = str(fixture["change_id"])
        change_dir = root / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True)
        (change_dir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
        reg = FIXTURES / "registry_snapshot.yaml"
        checks = list(fixture["required_checks"])
        draft_plan(change_id, checks, root=root, registry_path=reg)
        plan = read_plan(change_id, root=root)
        snap = plan["implementation_snapshot"]
        _assert_v1_snapshot_schema_only(snap)
        assert "completion_policy" not in snap

        report = fetch_impl_completion_report(change_id, root=root)
        assert report.get("legacy_apply_required") or report.get("status") == "BLOCKED"

        try:
            freeze_plan(change_id, root=root, registry_path=reg)
        except BoundedVerifyPlanError as exc:
            assert exc.code == IMPL_COMPLETION_BLOCKED
        else:
            raise AssertionError("freeze must fail without completion authority")


def test_missing_completion_cannot_silently_pass_freeze() -> None:
    """Без projection/runner freeze fail-closed (не IMPL_OK)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "no-completion"
        change_dir = root / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True)
        (change_dir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
        reg = FIXTURES / "registry_snapshot.yaml"
        draft_plan(
            change_id,
            [{"check_id": "pyright-strict", "order": 1}],
            root=root,
            registry_path=reg,
        )
        try:
            assert_implementation_ready_for_freeze(change_id, root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code in (
                IMPL_COMPLETION_BLOCKED,
                IMPL_COMPLETION_UNKNOWN,
                IMPL_NOT_READY,
            )
        else:
            raise AssertionError("missing completion must not authorize IMPL_OK")


def test_stale_hermetic_marker_blocks_freeze() -> None:
    """Устаревший marker в projection не даёт freeze при живом digest drift."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = "stale-marker"
        change_dir = root / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True)
        (change_dir / "tasks.md").write_text("- [ ] incomplete\n", encoding="utf-8")
        seed_hermetic_impl_ok(change_id, root=root)
        reg = FIXTURES / "registry_snapshot.yaml"
        draft_plan(
            change_id,
            [{"check_id": "pyright-strict", "order": 1}],
            root=root,
            registry_path=reg,
        )
        write_hermetic_impl_projection(change_id, root=root, marker="IMPL_GAPS")
        try:
            assert_implementation_ready_for_freeze(change_id, root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == IMPL_NOT_READY
        else:
            raise AssertionError("stale marker must block freeze")

        plan = read_plan(change_id, root=root)
        try:
            assert_snapshot_fresh(plan, change_id, root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "BLOCKED_SNAPSHOT_DRIFT"
        else:
            raise AssertionError("stale completion must drift content_digest vs frozen draft")


def main() -> int:
    """Прогон compose-регрессии как скрипт bounded-verify runner."""
    cases = (
        ("SPLIT_APPLY_FROZEN_READ", test_split_apply_frozen_read_contract),
        ("SPLIT_APPLY_HERMETIC_IMPL_OK", test_split_apply_impl_ok_via_hermetic_completion_report),
        ("LEGACY_TASKS_FREEZE_BLOCKED", test_legacy_tasks_freeze_blocked_without_completion_authority),
        ("MISSING_COMPLETION_FAIL_CLOSED", test_missing_completion_cannot_silently_pass_freeze),
        ("STALE_MARKER_DRIFT", test_stale_hermetic_marker_blocks_freeze),
    )
    failed = 0
    for name, fn in cases:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
            continue
        print(f"  [PASS] {name}")
    print(f"BOUNDED_VERIFY_SPLIT_APPLY_TESTS: {len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
