"""Канон APPLY-DESIGN: повторное использование до вызова адаптера и петля partial submit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.adapters.abc_adapter import AgentResult
from agents.implementation.apply_design.agent import ApplyDesignAgent
from agents.implementation.apply_design.tool_runtime import ApplyDesignToolRuntime
from workflows.artifact_paths import apply_design_artifact_path
from workflows.implementation.design_machine_validation import (
    DesignDependentSnapshotResult,
    DesignMachineGateResult,
)


def _minimal_ready_design(change_id: str) -> dict[str, object]:
    return {
        "schema": "opsx.apply_design",
        "schema_version": 4,
        "change_id": change_id,
        "status": "APPLY_DESIGN_READY",
        "generated_at": "2026-11-12T00:00:00Z",
        "chat_history_required": False,
        "write_boundary": {"allowed_paths": []},
        "artifact_identity": {
            "machine_path": f"openspec/changes/{change_id}/apply-design/APPLY-DESIGN.json",
        },
        "next_stage": "APPLY_DESIGN_REVIEW",
        "planning_identity": {"planning_digest": "p"},
        "protected_context_identity": {"content_digest": "c"},
        "reuse_decisions": [{"decision": "new"}],
        "composition_completeness": {"invariant_classifications": [{"id": "x"}]},
        "blockers": [],
        "slices": [],
    }


def _seed_planning(root: Path, change_id: str) -> None:
    cap = root / "opsx" / "project"
    cap.mkdir(parents=True)
    (cap / "capabilities.yaml").write_text(
        "product:\n  source_roots:\n    - src/stroy\n",
        encoding="utf-8",
    )
    base = root / "openspec" / "changes" / change_id
    base.mkdir(parents=True)
    (base / "tasks.md").write_text("- [ ] t\n", encoding="utf-8")
    (base / "design.md").write_text("# d\n", encoding="utf-8")
    ad = base / "apply-design"
    ad.mkdir(parents=True)
    payload = _minimal_ready_design(change_id)
    (ad / "APPLY-DESIGN.json").write_text(
        json.dumps(payload, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_valid_on_disk_canon_reused_without_adapter_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    change_id = "visual"
    _seed_planning(tmp_path, change_id)
    canon = apply_design_artifact_path(tmp_path, change_id)
    before = canon.read_bytes()

    gate = DesignMachineGateResult(ok=True, status="PASS", exit_code=0, errors=[])
    snap = DesignDependentSnapshotResult(
        ok=True, code=None, frozen_digest="f", current_digest="f"
    )

    adapter = MagicMock()
    adapter.run = AsyncMock()

    agent = ApplyDesignAgent(adapter=adapter)
    state = {
        "change_id": change_id,
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "model": {"id": "composer-2.5"},
    }
    monkeypatch.setattr(
        "agents.implementation.apply_design.agent.validate_apply_design_machine_async",
        AsyncMock(return_value=gate),
    )
    monkeypatch.setattr(
        "agents.implementation.apply_design.agent.evaluate_apply_design_dependent_snapshot_async",
        AsyncMock(return_value=snap),
    )
    out = await agent.run(state)

    assert out.get("apply_design_status") == "NO_CHANGE"
    assert canon.read_bytes() == before
    adapter.run.assert_not_called()
    assert "work_scope_digest" not in canon.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_repair_intent_invokes_adapter_when_canon_reusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """При repair intent адаптер вызывается, даже если канон на диске валиден."""
    change_id = "visual"
    _seed_planning(tmp_path, change_id)
    gate = DesignMachineGateResult(ok=True, status="PASS", exit_code=0, errors=[])
    snap = DesignDependentSnapshotResult(
        ok=True, code=None, frozen_digest="f", current_digest="f"
    )

    async def _adapter_run(transport, **kwargs):
        parsed = MagicMock()
        parsed.model_dump.return_value = {
            "status": "APPLY_PARTIAL",
            "tasks_summary": "repair hop",
            "gap_closure_summary": "adapter execution result",
            "files_changed": [],
            "remaining_gaps": [],
        }
        return AgentResult(
            parsed=parsed,
            agent_id="a",
            run_id="r",
            status="ok",
            text="",
            usage={},
            cost=0,
            cost_status="ok",
            billing_error=None,
            usage_source="test",
            validation_attempts=1,
        )

    adapter = MagicMock()
    adapter.run = AsyncMock(side_effect=_adapter_run)
    agent = ApplyDesignAgent(adapter=adapter)
    state = {
        "change_id": change_id,
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "model": {"id": "composer-2.5"},
        "implementation_force_design_hop": True,
    }
    monkeypatch.setattr(
        "agents.implementation.apply_design.agent.validate_apply_design_machine_async",
        AsyncMock(return_value=gate),
    )
    monkeypatch.setattr(
        "agents.implementation.apply_design.agent.evaluate_apply_design_dependent_snapshot_async",
        AsyncMock(return_value=snap),
    )
    out = await agent.run(state)  # type: ignore[arg-type]

    adapter.run.assert_called_once()


@pytest.mark.asyncio
async def test_partial_submit_with_valid_canon_stays_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Невалидный submit при PASS gate на каноне — BLOCKED, streak растёт, не NO_CHANGE."""
    change_id = "visual"
    _seed_planning(tmp_path, change_id)
    gate_ok = DesignMachineGateResult(ok=True, status="PASS", exit_code=0, errors=[])
    snap = DesignDependentSnapshotResult(
        ok=True, code=None, frozen_digest="f", current_digest="f"
    )

    async def _adapter_run(transport, **kwargs):
        runtime = transport["apply_design_tool_runtime"]
        assert isinstance(runtime, ApplyDesignToolRuntime)
        _ = await runtime.submit_apply_design(
            {
                "content": '{"status":"APPLY_DESIGN_READY"}',
                "base_digest": runtime.current_revision_digest(),
            },
            None,
        )
        parsed = MagicMock()
        parsed.model_dump.return_value = {
            "status": "APPLY_PARTIAL",
            "tasks_summary": "partial",
            "gap_closure_summary": "partial json",
            "files_changed": [],
            "remaining_gaps": [],
        }
        return AgentResult(
            parsed=parsed,
            agent_id="a",
            run_id="r",
            status="ok",
            text="",
            usage={},
            cost=0,
            cost_status="ok",
            billing_error=None,
            usage_source="test",
            validation_attempts=1,
        )

    adapter = MagicMock()
    adapter.run = AsyncMock(side_effect=_adapter_run)
    agent = ApplyDesignAgent(adapter=adapter)
    state = {
        "change_id": change_id,
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "model": {"id": "composer-2.5"},
        "implementation_force_design_hop": True,
    }
    monkeypatch.setattr(
        "agents.implementation.apply_design.agent.validate_apply_design_machine_async",
        AsyncMock(return_value=gate_ok),
    )
    monkeypatch.setattr(
        "agents.implementation.apply_design.agent.evaluate_apply_design_dependent_snapshot_async",
        AsyncMock(return_value=snap),
    )
    out = await agent.run(state)  # type: ignore[arg-type]

    assert out.get("apply_design_status") == "BLOCKED"
    assert out.get("implementation_design_submit_fail_streak") == 1
    assert out.get("implementation_design_submit_fail_code") == "DESIGN_INCOMPLETE"


@pytest.mark.asyncio
async def test_two_incomplete_submits_block_third_model_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    change_id = "visual"
    _seed_planning(tmp_path, change_id)
    canon = apply_design_artifact_path(tmp_path, change_id)
    canon_sha = hashlib.sha256(canon.read_bytes()).hexdigest()

    gate_ok = DesignMachineGateResult(ok=True, status="PASS", exit_code=0, errors=[])
    snap = DesignDependentSnapshotResult(
        ok=True, code=None, frozen_digest="f", current_digest="f"
    )

    calls = 0

    async def _adapter_run(transport, **kwargs):
        nonlocal calls
        calls += 1
        runtime = transport["apply_design_tool_runtime"]
        assert isinstance(runtime, ApplyDesignToolRuntime)
        _ = await runtime.submit_apply_design(
            {"content": '{"status":"APPLY_DESIGN_READY"}', "base_digest": runtime.current_revision_digest()},
            None,
        )
        parsed = MagicMock()
        parsed.model_dump.return_value = {
            "status": "APPLY_PARTIAL",
            "tasks_summary": "partial",
            "gap_closure_summary": "partial json",
            "files_changed": [],
            "remaining_gaps": [],
        }
        return AgentResult(
            parsed=parsed,
            agent_id="a",
            run_id="r",
            status="ok",
            text="",
            usage={},
            cost=0,
            cost_status="ok",
            billing_error=None,
            usage_source="test",
            validation_attempts=1,
        )

    adapter = MagicMock()
    adapter.run = AsyncMock(side_effect=_adapter_run)

    agent = ApplyDesignAgent(adapter=adapter)
    base_state: dict[str, object] = {
        "change_id": change_id,
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "model": {"id": "composer-2.5"},
        "implementation_force_design_hop": True,
        "implementation_hop_machine_repair_count": 1,
    }
    monkeypatch.setattr(
        "agents.implementation.apply_design.agent.validate_apply_design_machine_async",
        AsyncMock(return_value=gate_ok),
    )
    monkeypatch.setattr(
        "agents.implementation.apply_design.agent.evaluate_apply_design_dependent_snapshot_async",
        AsyncMock(return_value=snap),
    )

    out1 = await agent.run(dict(base_state))  # type: ignore[arg-type]
    assert out1.get("apply_design_status") == "BLOCKED"
    assert out1.get("implementation_design_submit_fail_streak") == 1
    state2 = {**base_state, **out1, "implementation_force_design_hop": True}
    out2 = await agent.run(state2)  # type: ignore[arg-type]
    assert out2.get("implementation_design_submit_fail_streak") == 2
    assert calls == 2

    state3 = {
        **base_state,
        **out2,
    }
    state3["implementation_force_design_hop"] = False
    state3["implementation_design_canon_sha256"] = canon_sha
    out3 = await agent.run(state3)  # type: ignore[arg-type]

    assert calls == 2
    assert out3.get("apply_design_status") == "BLOCKED"
    assert out3.get("implementation_recovery_code") == "DESIGN_SUBMIT_LOOP_EXHAUSTED"
