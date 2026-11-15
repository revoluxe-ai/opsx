"""Bounded-verify carrier bridge for Temporal verification preflight."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = ROOT.parents[1]
BV_ROOT = GAME_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = GAME_ROOT / ".cursor" / "pipeline" / "bounded-verify-tests" / "fixtures"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import draft_plan, freeze_plan  # noqa: E402
from verification_plan_bridge import (  # noqa: E402
    resolve_verification_plan_context,
)


def _load_fixture() -> dict:
    return json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))


def _setup_change(tmp_path: Path, change_id: str | None = None) -> str:
    fixture = _load_fixture()
    cid = change_id or str(fixture["change_id"])
    change_dir = tmp_path / "openspec" / "changes" / cid
    change_dir.mkdir(parents=True)
    (change_dir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
    return cid


def _registry_path() -> Path:
    return FIXTURES / "registry_snapshot.yaml"


def test_missing_plan_blocks_with_carrier_hint(tmp_path: Path):
    change_id = _setup_change(tmp_path)
    ctx = resolve_verification_plan_context(tmp_path, change_id)
    assert ctx.status == "BLOCKED"
    assert ctx.block_reason == "VERIFICATION_PLAN_MISSING"
    assert ctx.carrier == "bounded-verify/plan.json"
    assert "bounded-verify" in ctx.plan_carrier_path


def test_frozen_plan_preflight_ok(tmp_path: Path):
    fixture = _load_fixture()
    change_id = _setup_change(tmp_path)
    checks = list(fixture["required_checks"])
    draft_plan(
        change_id,
        checks,
        root=tmp_path,
        registry_path=_registry_path(),
    )
    frozen = freeze_plan(
        change_id,
        root=tmp_path,
        registry_path=_registry_path(),
    )
    ctx = resolve_verification_plan_context(tmp_path, change_id)
    assert ctx.status == "OK"
    assert ctx.plan_id == frozen.plan_id
    assert ctx.plan_fingerprint == frozen.plan_fingerprint
    assert len(ctx.plan_fingerprint or "") == 64
    payload = ctx.to_activity_payload()
    assert payload["VERIFICATION_PLAN_CARRIER"] == "bounded-verify/plan.json"


def test_draft_plan_not_frozen(tmp_path: Path):
    fixture = _load_fixture()
    change_id = _setup_change(tmp_path)
    draft_plan(
        change_id,
        list(fixture["required_checks"]),
        root=tmp_path,
        registry_path=_registry_path(),
    )
    ctx = resolve_verification_plan_context(tmp_path, change_id)
    assert ctx.status == "BLOCKED"
    assert ctx.block_reason == "PLAN_NOT_FROZEN"


def test_ledger_stale_identity_blocked(tmp_path: Path):
    fixture = _load_fixture()
    change_id = _setup_change(tmp_path)
    checks = list(fixture["required_checks"])
    draft_plan(change_id, checks, root=tmp_path, registry_path=_registry_path())
    frozen = freeze_plan(change_id, root=tmp_path, registry_path=_registry_path())
    ledger_dir = tmp_path / "openspec" / "changes" / change_id / "bounded-verify"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    stale = {
        "schema_version": "bounded-verify-ledger-v1",
        "ledger_id": "led-1",
        "plan_id": frozen.plan_id,
        "plan_fingerprint": "0" * 64,
        "ledger_revision": 1,
        "ledger_status": "ACTIVE",
        "entries": [
            {"check_id": checks[0]["check_id"], "order": 1, "state": "PENDING"},
        ],
        "current_assignment": None,
    }
    (ledger_dir / "ledger.json").write_text(
        json.dumps(stale, indent=2),
        encoding="utf-8",
    )
    ctx = resolve_verification_plan_context(tmp_path, change_id)
    assert ctx.status == "BLOCKED"
    assert ctx.block_reason == "LEDGER_STALE_PLAN_IDENTITY"


@pytest.mark.asyncio
async def test_ensure_verification_context_uses_bridge(tmp_path: Path):
    from activities import ActivityConfig, configure_activities, ensure_verification_context

    fixture = _load_fixture()
    change_id = _setup_change(tmp_path)
    draft_plan(
        change_id,
        list(fixture["required_checks"]),
        root=tmp_path,
        registry_path=_registry_path(),
    )
    freeze_plan(change_id, root=tmp_path, registry_path=_registry_path())
    configure_activities(
        ActivityConfig(mode="real", repo_path=str(tmp_path), change=change_id, lane="verification")
    )
    result = await ensure_verification_context({"change": change_id})
    assert result["status"] == "OK"
    assert result["plan_id"]
    assert result["VERIFICATION_PLAN_CARRIER"] == "bounded-verify/plan.json"
