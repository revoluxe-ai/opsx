"""Only fully clean no-write coverage activation may be reused."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.check_receipts import best_effort_write_pass
from agents.verify.check_reuse import review_receipt_key
from agents.verify.coverage_activation.agent import CoverageActivationAgent
from agents.verify.coverage_activation.git_inventory import ChangeInventory
from agents.verify.coverage_activation.schemas import CoverageActivationAnswer


def _setup(root):
    change = root / "openspec/changes/demo"
    change.mkdir(parents=True)
    (change / "design.md").write_text("contract\n", encoding="utf-8")
    src = root / "src/stroy"
    src.mkdir(parents=True)
    (src / "service.py").write_text("x = 1\n", encoding="utf-8")
    policy = root / "opsx/opsx_runtime/agents/verify/coverage_activation/agent.py"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("contract = 1\\n", encoding="utf-8")


def _inventory(status="READY"):
    return ChangeInventory(
        base_sha="baseline", audit_target="WORKTREE",
        current_changed=("src/stroy/service.py",),
        historical_touched=(), deleted_paths=(),
        status=status, block_reason="GIT_BASE_UNRESOLVED" if status == "BLOCKED" else "",
    )


@pytest.mark.asyncio
async def test_noop_activation_reused_without_sdk_or_static_mutation(tmp_path, monkeypatch):
    _setup(tmp_path)
    inv = _inventory()
    monkeypatch.setattr(
        "agents.verify.coverage_activation.agent.collect_change_inventory",
        lambda *a, **kwargs: inv,
    )
    state = {
        "repository_path": str(tmp_path), "change_id": "demo",
        "run_uuid": "exec-2", "model": {"id": "composer-2.5"},
    }
    key = review_receipt_key(
        state, role="coverage_activation",
        prompt=f"coverage_activation_inventory_v1:{inv.fingerprint()}",
    )
    assert key is not None
    answer = CoverageActivationAnswer(
        verdict="COVERAGE_ACTIVATION_PASS",
        summary="No pending activation",
        test_coverage_gaps=[],
        local_hop_copy_block="Workspace: demo\nVerdict: PASS",
    )
    metrics = {
        "static_activated": 0, "tests_reactivated": 0,
        "mypy_registered": 0, "blocked": 0,
    }
    assert best_effort_write_pass(
        root=tmp_path, change_id="demo", check="coverage_activation",
        contract="coverage_activation_noop_v1",
        input_digest=key.digest,
        verifier_contract_digest=key.verifier_contract_digest,
        origin_execution_id="exec-1",
        evidence_id="deterministic-noop",
        pass_verdict="COVERAGE_ACTIVATION_PASS",
        result={
            "verdict": "COVERAGE_ACTIVATION_PASS",
            "answer": answer.model_dump(mode="json"),
            "metrics": metrics, "checks": {},
        },
    )
    adapter = MagicMock()
    adapter.run = AsyncMock(side_effect=AssertionError("must not call model"))
    patch = await CoverageActivationAgent(adapter).run(state)
    assert patch["coverage_activation_verdict"] == "COVERAGE_ACTIVATION_PASS"
    assert patch["coverage_activation_complete"] is True
    assert patch["coverage_activation_skipped_resume"] is True
    assert patch["coverage_activation_execution_status"] == "REUSED_PASS"
    assert patch["coverage_activation_receipt"]["origin_execution_id"] == "exec-1"
    adapter.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocked_inventory_never_inherits_noop_pass(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(
        "agents.verify.coverage_activation.agent.collect_change_inventory",
        lambda *a, **kwargs: _inventory("BLOCKED"),
    )
    state = {
        "repository_path": str(tmp_path), "change_id": "demo",
        "run_uuid": "exec-2", "model": {"id": "composer-2.5"},
    }
    adapter = MagicMock()
    adapter.run = AsyncMock(side_effect=AssertionError("blocked preflight"))
    patch = await CoverageActivationAgent(adapter).run(state)
    assert patch["coverage_activation_verdict"] == "COVERAGE_ACTIVATION_BLOCKED"
    assert patch.get("coverage_activation_execution_status") != "REUSED_PASS"
