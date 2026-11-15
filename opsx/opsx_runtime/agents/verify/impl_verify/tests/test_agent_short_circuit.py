"""Unit tests: PHASE_IMPL verify agent — единый путь через адаптер."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.adapters.abc_adapter import AgentResult
from agents.state import GraphState
from agents.verify.impl_verify.agent import ImplVerifyAgent
from agents.verify.impl_verify.schemas import VerifyImplAnswer


@pytest.mark.asyncio
async def test_impl_verify_calls_adapter_and_validates_answer() -> None:
    """Агент всегда вызывает адаптер и возвращает ``VerifyImplAnswer`` в outcome."""
    change_id = "ok-change"
    preflight = {
        "change_id": change_id,
        "change_exists": True,
        "artifact_bundle_meta": {"missing": [], "spec_paths": []},
    }
    parsed = VerifyImplAnswer(
        verdict="IMPL_OK",
        behavior_summary="ok",
        architecture_placement="ok",
        tasks_consistency="ok",
        gap_details=[],
        next_command="",
        local_hop_copy_block="Workspace: /tmp\nChange: ok",
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
    agent = ImplVerifyAgent(adapter)

    with (
        patch(
            "agents.verify.impl_verify.agent.collect_deterministic_preflight",
            return_value=preflight,
        ),
        patch(
            "agents.verify.impl_verify.agent.load_change_artifact_bundle",
            return_value={"change_id": change_id, "artifacts": {}, "missing": []},
        ),
        patch(
            "agents.verify.impl_verify.agent.get_prompt",
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

    adapter.run.assert_called_once()
    validated = VerifyImplAnswer.model_validate(result["answer"])
    assert validated.verdict == "IMPL_OK"
    assert result["verdict"] == "IMPL_OK"


@pytest.mark.asyncio
async def test_impl_verify_does_not_override_ok_with_preflight() -> None:
    """Classifier/preflight не переводит IMPL_OK модели в IMPL_GAPS."""
    change_id = "ok-change"
    preflight = {
        "change_id": change_id,
        "change_exists": True,
        "artifact_bundle_meta": {"missing": [], "spec_paths": []},
    }
    parsed = VerifyImplAnswer(
        verdict="IMPL_OK",
        behavior_summary="ok",
        architecture_placement="ok",
        tasks_consistency="ok",
        gap_details=[],
        next_command="",
        local_hop_copy_block="Workspace: /tmp\nChange: ok",
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
    agent = ImplVerifyAgent(adapter)

    with (
        patch(
            "agents.verify.impl_verify.agent.collect_deterministic_preflight",
            return_value=preflight,
        ),
        patch(
            "agents.verify.impl_verify.agent.load_change_artifact_bundle",
            return_value={"change_id": change_id, "artifacts": {}, "missing": []},
        ),
        patch(
            "agents.verify.impl_verify.agent.get_prompt",
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

    assert result["verdict"] == "IMPL_OK"
    assert result["answer"]["gap_details"] == []
