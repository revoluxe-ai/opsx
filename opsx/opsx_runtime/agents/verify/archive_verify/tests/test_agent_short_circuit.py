"""Unit tests: PHASE_ARCHIVE verify agent — adapter + machine gaps."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.adapters.abc_adapter import AgentResult
from agents.state import GraphState
from agents.verify.archive_verify.agent import ArchiveVerifyAgent
from agents.verify.archive_verify.schemas import VerifyArchiveAnswer


@pytest.mark.asyncio
async def test_archive_verify_forces_no_go_on_machine_gaps() -> None:
    """Machine preflight gaps переводят модельный GO в NO_GO."""
    change_id = "archive-demo"
    preflight = {
        "change_id": change_id,
        "change_exists": True,
        "artifact_bundle_meta": {"missing": [], "spec_paths": []},
        "tasks_progress": {"total": 2, "completed": 1, "remaining": 1},
        "config_contract": {"status": "PASS"},
        "channel_archive_gate": {"status": "SKIP"},
        "product_acceptance": {"status": "NOT_APPLICABLE"},
        "apply_completion_status": "IMPL_OK",
    }
    parsed = VerifyArchiveAnswer(
        verdict="GO",
        completeness_summary="ok",
        correctness_summary="ok",
        coherence_summary="ok",
        product_acceptance_status="NOT_APPLICABLE",
        gap_details=[],
        warnings=[],
        local_hop_copy_block="Workspace: /tmp\nChange: archive-demo",
    )
    adapter = MagicMock()
    adapter.run = AsyncMock(
        return_value=AgentResult(
            text="{}",
            parsed=parsed,
            agent_id="agent-1",
            run_id="run-1",
            status="finished",
            usage=None,
            cost=None,
            cost_status="ok",
            billing_error=None,
            usage_source="adapter",
            validation_attempts=1,
        ),
    )
    agent = ArchiveVerifyAgent(adapter)

    with (
        patch(
            "agents.verify.archive_verify.agent.collect_deterministic_preflight",
            return_value=preflight,
        ),
        patch(
            "agents.verify.archive_verify.agent.load_change_artifact_bundle",
            return_value={"change_id": change_id, "artifacts": {}, "missing": []},
        ),
        patch(
            "agents.verify.archive_verify.agent.get_prompt",
            return_value="prompt",
        ),
    ):
        result = await agent.run(
            GraphState(
                repository_path="/tmp/repo",
                workspace="/tmp/repo",
                change_id=change_id,
            ),
        )

    assert result["verdict"] == "NO_GO"
    assert result["marker"] == "❌ NO_GO"
    assert any("IMPLEMENTATION_TRACK_INCOMPLETE" in g for g in result["answer"]["gap_details"])
