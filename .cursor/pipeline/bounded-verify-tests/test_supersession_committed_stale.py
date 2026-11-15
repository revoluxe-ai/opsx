# -*- coding: utf-8 -*-
"""COMMITTED pending must not block a new supersession when snapshot drifted."""

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

from bounded_verify_plan import draft_plan, freeze_plan, plan_carrier_path, read_plan  # noqa: E402
from bounded_verify_supersession import (  # noqa: E402
    generation_path,
    pending_path,
    publish_successor_plan,
)


def _setup(root: Path) -> str:
    cap = root / "opsx" / "project" / "capabilities.yaml"
    cap.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "opsx" / "project" / "capabilities.yaml", cap)
    fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
    change_id = str(fixture["change_id"])
    cdir = root / "openspec" / "changes" / change_id
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")
    (root / "src/stroy").mkdir(parents=True, exist_ok=True)
    (root / "src/stroy" / "placeholder.py").write_text("x=1\n", encoding="utf-8")
    for rel in ("tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    from test_impl_seed import seed_hermetic_impl_ok

    seed_hermetic_impl_ok(change_id, root=root)
    return change_id


def test_committed_pending_allows_supersession_after_impl_drift() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup(root)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "pyright-strict", "order": 1}]
        draft_plan(change_id, checks, root=root, registry_path=registry)
        freeze_plan(change_id, root=root, registry_path=registry)
        first = publish_successor_plan(change_id, checks, root=root, registry_path=registry)
        assert not first.noop
        pending = json.loads(pending_path(change_id, root=root).read_text(encoding="utf-8"))
        assert pending.get("status") == "COMMITTED"

        plan = read_plan(change_id, root=root)
        live_digest = str(plan["implementation_snapshot"]["content_digest"])
        stale = json.loads(plan_carrier_path(change_id, root=root).read_text(encoding="utf-8"))
        stale["implementation_snapshot"] = {"content_digest": "0" * 64}
        plan_carrier_path(change_id, root=root).write_text(
            json.dumps(stale, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        second = publish_successor_plan(change_id, checks, root=root, registry_path=registry)
        assert not second.noop
        assert second.new_plan_id != first.new_plan_id
        assert second.generation == first.generation + 1
        gen = json.loads(generation_path(change_id, root=root).read_text(encoding="utf-8"))
        assert int(gen["generation"]) == second.generation
        refreshed = read_plan(change_id, root=root)
        assert str(refreshed["implementation_snapshot"]["content_digest"]) == live_digest


def test_committed_pending_idempotent_replay_when_carriers_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change_id = _setup(root)
        registry = FIXTURES / "registry_snapshot.yaml"
        checks = [{"check_id": "pyright-strict", "order": 1}]
        draft_plan(change_id, checks, root=root, registry_path=registry)
        freeze_plan(change_id, root=root, registry_path=registry)
        first = publish_successor_plan(change_id, checks, root=root, registry_path=registry)
        replay = publish_successor_plan(change_id, checks, root=root, registry_path=registry)
        assert replay.noop
        assert replay.new_plan_id == first.new_plan_id


def main() -> int:
    test_committed_pending_allows_supersession_after_impl_drift()
    test_committed_pending_idempotent_replay_when_carriers_match()
    print("supersession committed-stale tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
